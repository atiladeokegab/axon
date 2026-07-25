"""Pose ingest API - the boundary between the pose estimator and our control.

The pose estimator is a TEAMMATE'S service; we do not implement vision here.
We define and own this contract so their side can be swapped freely.

ACCEPTED MESSAGE (JSON, UDP datagram):
    {
      "shoulder": [x, y, z],
      "elbow":    [x, y, z],
      "wrist":    [x, y, z],
      "timestamp": 1721740000.123     # seconds, float (optional but preferred)
    }
Positions in metres, subject-centred frame: +X forward, +Y left, +Z up.

ALTERNATIVE (if their side already computes angles):
    {"elbow": 90.0, "shoulder_flex": 30.0, "shoulder_abd": 15.0,
     "timestamp": ...}
Detected automatically by type (number vs list).

DESIGN NOTES
  * Stale poses are REFUSED, never reused. Driving a limb from an old pose is
    the failure mode most likely to hurt someone.
  * Angles are low-pass filtered: vision output jitters, and differentiating
    or reacting to that jitter would chatter the relays.
  * Runs on its own thread so a slow/absent estimator cannot stall control.
"""

import json
import socket
import threading
import time

from kinematics import joints_from_pose
import settings as C


class PoseReceiver:
    """Threaded UDP listener that maintains the latest filtered joint angles."""

    JOINTS = ("elbow", "shoulder_flex", "shoulder_abd")

    def __init__(self, host=None, port=None, alpha=None, stale_ms=None):
        self.host = host or C.POSE_LISTEN_HOST
        self.port = port or C.POSE_LISTEN_PORT
        self.alpha = C.POSE_FILTER_ALPHA if alpha is None else alpha
        self.stale_ms = C.POSE_STALE_MS if stale_ms is None else stale_ms

        self._lock = threading.Lock()
        self._filtered = None       # dict of joint -> deg
        self._raw = None
        self._last_rx = 0.0
        self._count = 0
        self._bad = 0
        self._last_ts = None        # sender's last timestamp (freeze detection)
        self._stale_ts = 0          # messages dropped for a non-advancing clock
        self._running = False
        self._thread = None
        self._sock = None

    # ---- lifecycle --------------------------------------------------------
    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(0.2)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[pose] listening on %s:%d" % (self.host, self.port))
        return self

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._sock:
            self._sock.close()

    # ---- receive ----------------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode())
                if self._frozen(msg):
                    # Sender's timestamp is not advancing: it has lost tracking
                    # and is repeating itself. Do NOT refresh _last_rx, so the
                    # pose ages out and stimulation stops.
                    self._stale_ts += 1
                    continue
                joints = self._parse(msg)
            except Exception:
                self._bad += 1
                continue
            if joints is None:
                self._bad += 1
                continue
            self._ingest(joints)

    def _frozen(self, msg):
        """True if the sender's own timestamp has stopped advancing.

        POSE_API.md tells the estimator it may signal loss of tracking by
        sending an old timestamp. That contract was previously unimplemented -
        staleness was judged purely on ARRIVAL time, so an estimator dutifully
        re-sending a frozen pose kept the controller driving a limb toward a
        position it could no longer see. This closes that hole.

        Compared against the SENDER's previous timestamp, never against our own
        clock, so the two machines do not need synchronised time.
        """
        ts = msg.get("timestamp")
        if not isinstance(ts, (int, float)):
            return False                       # no timestamp: fall back to arrival age
        if self._last_ts is not None and ts <= self._last_ts:
            return True                        # not advancing => frozen
        self._last_ts = ts
        return False

    def _parse(self, msg):
        """Accept either 3D landmarks or pre-computed angles."""
        if not isinstance(msg, dict):
            return None

        # Pre-computed angles?
        if isinstance(msg.get("elbow"), (int, float)):
            return {j: float(msg.get(j, 0.0)) for j in self.JOINTS}

        # 3D landmarks
        s, e, w = msg.get("shoulder"), msg.get("elbow"), msg.get("wrist")
        if not (isinstance(s, (list, tuple)) and len(s) == 3):
            return None
        if not (isinstance(e, (list, tuple)) and len(e) == 3):
            return None
        if not (isinstance(w, (list, tuple)) and len(w) == 3):
            return None
        return joints_from_pose(tuple(map(float, s)),
                                tuple(map(float, e)),
                                tuple(map(float, w)))

    def _ingest(self, joints):
        with self._lock:
            self._raw = joints
            if self._filtered is None:
                self._filtered = dict(joints)
            else:
                a = self.alpha
                for j in self.JOINTS:
                    self._filtered[j] = a * joints[j] + (1 - a) * self._filtered[j]
            self._last_rx = time.monotonic()
            self._count += 1

    # ---- read -------------------------------------------------------------
    def latest(self):
        """Return (joints_dict, fresh_bool). Never returns a stale pose as fresh."""
        with self._lock:
            if self._filtered is None:
                return None, False
            age_ms = (time.monotonic() - self._last_rx) * 1000.0
            return dict(self._filtered), age_ms <= self.stale_ms

    def stats(self):
        with self._lock:
            age = (time.monotonic() - self._last_rx) * 1000.0 if self._count else None
            return {"received": self._count, "malformed": self._bad,
                    "frozen_ts": self._stale_ts,
                    "age_ms": None if age is None else round(age, 1)}


class SimulatedPoseSource:
    """A stand-in arm for testing with no estimator and no hardware.

    Models the limb as a first-order lag toward wherever the stimulation is
    pushing it, plus gravity pulling every joint back toward rest. Crude, but
    it exercises the full control path: PI -> duty -> 'muscle' -> angle -> PI.
    """

    def __init__(self, gain=130.0, tau=0.45, gravity=18.0):
        # gain: degrees of steady deflection at duty=1.0. Set so that the
        # usable range (duty <= DUTY_MAX = 0.7) still reaches ~90 deg of elbow
        # flexion, which is roughly what strong surface FES on biceps achieves.
        self.gain = gain          # deg of steady deflection at full duty
        self.tau = tau            # first-order time constant (s)
        self.gravity = gravity    # deg/s pull back toward rest
        self.joints = {"elbow": 10.0, "shoulder_flex": 0.0, "shoulder_abd": 0.0}

    def step(self, agonist_duty, dt):
        """agonist_duty: dict joint -> signed duty (+ raises, - lowers)."""
        for j, val in self.joints.items():
            duty = agonist_duty.get(j, 0.0)
            target = duty * self.gain
            # first-order approach to the stimulation-driven target
            val += (target - val) * (dt / max(self.tau, 1e-3))
            # gravity always pulls toward the rest pose
            val -= self.gravity * dt * (1.0 if val > 0 else -1.0) * 0.15
            self.joints[j] = max(-30.0, min(150.0, val))
        return dict(self.joints)

    def latest(self):
        return dict(self.joints), True

    def stats(self):
        return {"simulated": True}
