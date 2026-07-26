"""The control loop, running inside the web app.

Owns one ArmController, ticks it at CONTROL_RATE_HZ, and exposes the same
operations the keyboard offers in controller/run.py. The browser is a view over
this; it holds no control state of its own, so two open tabs cannot disagree
about whether the system is armed.

SAFETY POSTURE - read before changing anything here.

* The board is the safety layer, not this. It clamps duty, enforces
  burst/cooldown, refuses antagonist co-contraction and opens every relay if
  these packets stop arriving. Nothing in this file can widen those limits.
* It boots DISARMED and every new connection is told so explicitly.
* A kill LATCHES, exactly as it does in the terminal client, and needs a
  deliberate re-arm.
* THE LOOP KEEPS RUNNING WHEN THE LAST BROWSER DISCONNECTS. That is a
  deliberate choice and the opposite of what a web app usually does: silently
  stopping the packets would trip the board's watchdog and drop the arm
  mid-movement. Instead the loop stays up and holds, and a closed tab does not
  become an uncommanded release. Use the stop button, or close the app.
"""

import asyncio
import time

import human_control  # noqa: F401  - installs the controller/ import bridge

import settings as C
import mapping
from control_loop import ArmController
from link import EspLink, NullLink

from .pose_bridge import InProcessPoseSource

# Jog steps come from the shared settings so the web UI and the keyboard move
# the arm by identical amounts. Two different step sizes would make the tab and
# the terminal feel like different machines.
JOG = C.JOG_STEP_DEG

# Axis -> (joint, sign). Mirrors the key handling in controller/run.py:
#   up/down  elevation     CH5 middle deltoid / gravity
#   fwd/back forward-back  CH3 anterior / CH4 posterior deltoid
#   flex/ext elbow         CH1 biceps / CH2 triceps
ACTIONS = {
    "up":      ("shoulder_abd", +1.0),
    "down":    ("shoulder_abd", -1.0),
    "forward": ("shoulder_flex", +1.0),
    "back":    ("shoulder_flex", -1.0),
    "flex":    ("elbow", +1.0),
    "extend":  ("elbow", -1.0),
}


class HumanControlService:
    """One control loop for the whole site."""

    def __init__(self, runtime):
        self._runtime = runtime          # Live Twin's LiveRuntime
        self._pose = None
        self._ctl = None
        self._task = None
        self._lock = asyncio.Lock()
        self._running = False
        self._subscribers = set()
        self._board_host = None
        self._simulated = True
        self.last_error = None
        self.notice = None

    # ---- lifecycle --------------------------------------------------------
    async def start(self, board_host=None, simulated=True):
        """Acquire the shared camera and begin ticking. Idempotent."""
        async with self._lock:
            if self._running:
                return self.status()

            self._simulated = bool(simulated)
            self._board_host = board_host

            # Holds the camera open for as long as this service is live, the
            # same way a Live Twin websocket client does.
            await self._runtime.acquire()
            try:
                self._pose = await InProcessPoseSource(
                    self._runtime.broadcaster).start()
                # NullLink means the loop runs and reports, but no packet ever
                # reaches the board. It is the honest default: arming should be
                # a decision, not a side effect of opening a tab.
                board = NullLink() if self._simulated else EspLink(host=board_host)
                self._ctl = ArmController(board, self._pose)
                self._running = True
                self._task = asyncio.create_task(self._loop())
            except Exception:
                await self._runtime.release()
                raise
            return self.status()

    async def stop(self):
        async with self._lock:
            if not self._running:
                return
            self._running = False
            if self._ctl is not None:
                # Disarm before tearing anything down, so the last thing the
                # board hears is an explicit stop rather than silence.
                try:
                    self._ctl.disarm()
                    self._ctl.step()
                except Exception:
                    pass
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            if self._pose is not None:
                await self._pose.stop()
                self._pose = None
            self._ctl = None
            await self._runtime.release()

    # ---- the loop ---------------------------------------------------------
    async def _loop(self):
        period = 1.0 / max(C.CONTROL_RATE_HZ, 1)
        while self._running:
            began = time.monotonic()
            try:
                self._ctl.step()
                self.last_error = None
            except Exception as exc:
                # Report rather than die. A loop that vanishes on one bad frame
                # stops feeding the watchdog, and the operator's first clue is
                # the arm dropping.
                self.last_error = repr(exc)
            await self._broadcast()
            elapsed = time.monotonic() - began
            await asyncio.sleep(max(0.0, period - elapsed))

    # ---- operator commands ------------------------------------------------
    def arm(self):
        if self._ctl is None:
            return "not running"
        self._ctl.arm()
        return "armed"

    def disarm(self):
        if self._ctl is None:
            return "not running"
        self._ctl.disarm()
        return "disarmed"

    def kill(self):
        if self._ctl is None:
            return "not running"
        self._ctl.kill()
        return "EMERGENCY STOP - latched, press ARM to recover"

    def set_grip(self, closed):
        if self._ctl is None:
            return "not running"
        self._ctl.set_grip(bool(closed))
        return "grip %s" % ("closed" if closed else "open")

    def jog(self, action, steps=1):
        """Move a target. Returns a human-readable note, or None."""
        if self._ctl is None:
            return "not running"
        if action not in ACTIONS:
            return "unknown action %r" % (action,)
        joint, sign = ACTIONS[action]
        moved = self._ctl.jog(joint, sign * JOG * float(steps))
        if not moved:
            lo, hi = C.JOINT_LIMITS[joint]
            # Naming the reason matters: a control that silently does nothing
            # is the single most confusing thing about this UI.
            return "%s is at its limit (range %.0f to %.0f deg)" % (joint, lo, hi)
        return None

    # ---- status -----------------------------------------------------------
    def status(self):
        if self._ctl is None:
            return {"running": False, "armed": False, "killed": False,
                    "simulated": self._simulated, "board_host": self._board_host}
        st = self._ctl.status()
        st.update({
            "running": True,
            "simulated": self._simulated,
            "board_host": self._board_host,
            "channels": mapping.describe(st["duties"]),
            "pose_stats": self._pose.stats() if self._pose else {},
            "loop_error": self.last_error,
            "jog_step_deg": JOG,
            "limits": C.JOINT_LIMITS,
        })
        return st

    # ---- fan-out to browsers ---------------------------------------------
    def subscribe(self):
        q = asyncio.Queue(maxsize=4)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self._subscribers.discard(q)

    async def _broadcast(self):
        if not self._subscribers:
            return
        payload = self.status()
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # A browser that cannot keep up gets dropped frames, never
                # backpressure onto the control loop.
                pass
