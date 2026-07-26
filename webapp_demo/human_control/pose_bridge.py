"""Feed the control loop from Live Twin's in-process camera.

controller/pose_api.PoseReceiver binds UDP 9090 and waits for datagrams. Inside
the site that hop is pointless: Live Twin's PoseBroadcaster already has the
landmarks in memory. This presents the same interface the control loop expects -
`latest()` returning (joints, fresh) - but sources it from the broadcaster.

WHY REUSE PoseReceiver RATHER THAN REIMPLEMENT ITS FILTERING. The receiver does
considerably more than hold a value: a physiological rate gate, a median window,
a one-euro adaptive low-pass, sender-timestamp freeze detection, and a live
noise estimate. All of that was tuned against real captures and is the
difference between an arm that settles and one that hunts. Reimplementing it
here would guarantee the two paths diverge, so instead the payload is converted
to the documented frame and pushed through the real receiver's own ingest.
"""

import asyncio
import time

import human_control  # noqa: F401  - installs the controller/ import bridge

from pose_api import PoseReceiver
from live_twin.backend.pose.control_link import to_control_frame

JOINT_KEYS = ("shoulder", "elbow", "wrist")


class InProcessPoseSource:
    """PoseReceiver fed from the shared broadcaster instead of a UDP socket.

    Exposes exactly the surface control_loop.ArmController uses: latest(),
    stats(), noise_sd(). It is deliberately NOT a subclass - it owns a receiver
    and drives it, so nothing here can accidentally change how the filtering
    behaves for the UDP path that controller/run.py still uses.
    """

    def __init__(self, broadcaster, stale_ms=None):
        self._broadcaster = broadcaster
        self._rx = PoseReceiver(stale_ms=stale_ms)
        self._queue = None
        self._task = None
        self._running = False
        # Counters worth surfacing: a pose feed that is arriving but never
        # usable looks identical to one that is not arriving at all.
        self.received = 0
        self.untracked = 0
        self.last_status = None

    # ---- lifecycle --------------------------------------------------------
    async def start(self):
        if self._running:
            return self
        self._queue = self._broadcaster.subscribe()
        self._running = True
        self._task = asyncio.create_task(self._pump())
        return self

    async def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._queue is not None:
            self._broadcaster.unsubscribe(self._queue)
            self._queue = None

    # ---- ingest -----------------------------------------------------------
    async def _pump(self):
        while self._running:
            try:
                payload = await self._queue.get()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            try:
                self._ingest(payload)
            except Exception:
                # A malformed frame must never kill the pump. If it did, the
                # pose would silently stop updating and the loop would keep
                # driving toward a target from a frozen measurement - the
                # failure mode POSE_STALE_MS exists to prevent.
                self.untracked += 1

    def _ingest(self, payload):
        if not isinstance(payload, dict):
            return
        self.last_status = payload.get("status")

        world = payload.get("world_landmarks")
        if not world or any(k not in world for k in JOINT_KEYS):
            # Not tracking. Deliberately do NOT touch the receiver: letting the
            # pose age out is what stops stimulation, and substituting a guess
            # here would defeat that.
            self.untracked += 1
            return

        # MediaPipe world axes -> the frame documented in POSE_API.md. Reusing
        # Live Twin's own converter rather than repeating the sign conventions,
        # because getting them wrong drives the limb the wrong way and does not
        # fail loudly.
        try:
            s, e, w = (tuple(to_control_frame(world[k])) for k in JOINT_KEYS)
        except (TypeError, ValueError, KeyError):
            self.untracked += 1
            return

        joints = self._rx._parse({"shoulder": list(s), "elbow": list(e),
                                  "wrist": list(w)})
        if joints is None:
            self.untracked += 1
            return
        self._rx._ingest(joints)
        self.received += 1

    # ---- the interface ArmController consumes -----------------------------
    def latest(self):
        return self._rx.latest()

    def noise_sd(self, joint=None):
        return self._rx.noise_sd(joint)

    def stats(self):
        st = self._rx.stats()
        st.update({"source": "in-process (shared camera)",
                   "ingested": self.received,
                   "untracked_frames": self.untracked,
                   "tracking": self.last_status})
        return st
