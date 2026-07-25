"""The closed loop: targets in, joint angles back, muscle duties out.

    keyboard  ->  target joint angles
                        |
                  (target - measured)  per joint
                        |
                  PI controller  -> signed effort
                        |
                  mapping        -> 8 channel duties
                        |
                  UDP            -> ESP32 -> relays -> muscles
                        |
    pose estimator  <-  the arm actually moves
                        |
                  measured joint angles  (back to the top)

Kept free of I/O specifics so it can be driven by a real pose receiver and a
real ESP32, or by the simulator, without changing a line.
"""

import time

import settings as C
import mapping
from kinematics import clamp_joint
from pid import PIController


class ArmController:
    JOINTS = ("elbow", "shoulder_flex", "shoulder_abd")

    def __init__(self, link, pose_source, duty_max=None):
        self.link = link
        self.pose = pose_source
        self.duty_max = C.DUTY_MAX if duty_max is None else duty_max

        self.pids = {}
        for joint in self.JOINTS:
            kp, ki, deadband, ilim = C.GAINS[joint]
            self.pids[joint] = PIController(
                kp=kp, ki=ki, deadband=deadband, i_limit=ilim,
                out_min=-self.duty_max, out_max=self.duty_max,
            )

        # Targets start at the rest pose; teleop nudges them from here.
        self.targets = {"elbow": 10.0, "shoulder_flex": 0.0, "shoulder_abd": 0.0}
        self.measured = dict(self.targets)
        self.efforts = {j: 0.0 for j in self.JOINTS}
        self.duties = {ch: 0.0 for ch in mapping.CHANNEL_ORDER}

        self.grip = False
        self.armed = False
        self.killed = False
        self.pose_ok = False
        self.last_fault = None
        self._last_t = time.monotonic()

        # What the BOARD reports about itself. Without this the UI only shows
        # what the controller *intended*, so a board that rebooted, disarmed or
        # went off the network looks identical to one that is working - and you
        # cannot run bench.py alongside to check, because the two would fight
        # over the link.
        self.board_status = None
        self.board_seen_at = None
        self.board_rebooted = False
        self._last_uptime = None
        # Timer keep-alive press counter, so a fault can be correlated against
        # the exact moment the TIMER relay fires.
        self._last_timer_presses = None
        self.timer_pressed_at = None

    # ---- operator commands ------------------------------------------------
    def jog(self, joint, delta_deg):
        """Nudge a joint target, respecting joint limits.

        Returns True if the target actually moved, False if it was clamped at a
        joint limit. The caller uses this to tell the operator why a keypress
        appeared to do nothing - silent clamping is deeply confusing otherwise.
        """
        if joint not in self.targets:
            return False
        before = self.targets[joint]
        self.targets[joint] = clamp_joint(
            joint, before + delta_deg, C.JOINT_LIMITS)
        return self.targets[joint] != before

    def within_deadband(self, joint):
        """True if this joint's error is too small to produce any stimulation.

        The controller deliberately does nothing inside the deadband (it stops
        the arm buzzing at the setpoint), but from the outside that looks
        identical to 'broken', so the UI needs to be able to say which it is.
        """
        deadband = C.GAINS[joint][2]
        error = self.targets[joint] - self.measured.get(joint, 0.0)
        return abs(error) < deadband, deadband

    def set_grip(self, closed):
        self.grip = bool(closed)

    def arm(self):
        for p in self.pids.values():
            p.reset()
        self.killed = False
        self.armed = True
        self.link.arm()

    def disarm(self):
        self.armed = False
        self._zero()
        self.link.disarm()

    def kill(self):
        """Operator e-stop: latch off until an explicit re-arm."""
        self.killed = True
        self.armed = False
        self.grip = False
        self._zero()
        # Send repeatedly - UDP is lossy and this is the one message that
        # absolutely must land.
        for _ in range(5):
            self.link.kill()
        self.last_fault = "operator_estop"

    def _zero(self):
        self.efforts = {j: 0.0 for j in self.JOINTS}
        self.duties = {ch: 0.0 for ch in mapping.CHANNEL_ORDER}
        for p in self.pids.values():
            p.reset()

    # ---- the loop ---------------------------------------------------------
    def step(self, now=None):
        """One control iteration. Returns a status dict for the UI."""
        if now is None:
            now = time.monotonic()
        dt = max(1e-3, now - self._last_t)
        self._last_t = now

        joints, fresh = self.pose.latest()
        self.pose_ok = fresh and joints is not None
        if joints is not None:
            self.measured = joints

        # Refuse to drive on a stale or missing pose. We stop commanding and
        # let the firmware watchdog take it from there.
        if self.killed or not self.armed or not self.pose_ok:
            if not self.pose_ok and self.armed:
                self.last_fault = "pose_stale"
            self._zero()
            if not self.killed:
                self.link.send_duties(mapping.duties_to_list(self.duties), False)
            return self.status()

        self.last_fault = None
        for joint in self.JOINTS:
            error = self.targets[joint] - self.measured.get(joint, 0.0)
            self.efforts[joint] = self.pids[joint].update(error, dt)

        self.duties = mapping.efforts_to_duties(
            self.efforts, grip=self.grip, duty_max=self.duty_max,
            min_effective=C.MIN_EFFECTIVE_DUTY,
            deadzone=C.DEADZONE_COMPENSATION)
        self.link.send_duties(mapping.duties_to_list(self.duties), self.grip)
        self._ingest_board_status()
        return self.status()

    def _ingest_board_status(self):
        """Record the board's heartbeat so the UI can show what IT thinks."""
        st = self.link.poll_status()
        if st is None:
            return
        self.board_status = st
        self.board_seen_at = time.monotonic()

        # A falling uptime means the board restarted - the single most useful
        # thing to know when "everything suddenly stopped", because a reboot
        # also clears the armed state and nothing will fire until you re-arm.
        up = st.get("uptime_ms")
        if isinstance(up, (int, float)):
            if self._last_uptime is not None and up < self._last_uptime:
                self.board_rebooted = True
            self._last_uptime = up

        # Note the instant the TIMER keep-alive relay fires. If stimulation
        # dies at the same moment, that is evidence the keep-alive coil is
        # disturbing the supply - the software path cannot block PWM.
        tp = st.get("timer_presses")
        if isinstance(tp, int):
            if self._last_timer_presses is not None and tp > self._last_timer_presses:
                self.timer_pressed_at = time.monotonic()
            self._last_timer_presses = tp

    def board_age_s(self):
        """Seconds since the last heartbeat, or None if never heard from."""
        if self.board_seen_at is None:
            return None
        return time.monotonic() - self.board_seen_at

    # ---- introspection ----------------------------------------------------
    def status(self):
        return {
            "armed": self.armed,
            "killed": self.killed,
            "pose_ok": self.pose_ok,
            "fault": self.last_fault,
            "grip": self.grip,
            "targets": {k: round(v, 1) for k, v in self.targets.items()},
            "measured": {k: round(v, 1) for k, v in self.measured.items()},
            "errors": {k: round(self.targets[k] - self.measured.get(k, 0.0), 1)
                       for k in self.JOINTS},
            "duties": {k: round(v, 3) for k, v in self.duties.items()},
        }
