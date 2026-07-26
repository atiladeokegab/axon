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
import math
import socket
import threading
import time

from filters import JointFilter
from kinematics import joints_from_pose
import settings as C


class PoseReceiver:
    """Threaded UDP listener that maintains the latest filtered joint angles."""

    JOINTS = ("elbow", "shoulder_flex", "shoulder_abd")

    def __init__(self, host=None, port=None, alpha=None, stale_ms=None,
                 median_n=None, mode=None, mincutoff=None, beta=None):
        self.host = host or C.POSE_LISTEN_HOST
        self.port = port or C.POSE_LISTEN_PORT
        self.alpha = C.POSE_FILTER_ALPHA if alpha is None else alpha
        self.stale_ms = C.POSE_STALE_MS if stale_ms is None else stale_ms
        # Odd window, so the median is always an actual sample.
        self.median_n = getattr(C, "POSE_MEDIAN_WINDOW", 5) if median_n is None else median_n
        self.mode = getattr(C, "POSE_FILTER_MODE", "oneeuro") if mode is None else mode
        self.mincutoff = (getattr(C, "POSE_ONEEURO_MINCUTOFF", 0.25)
                          if mincutoff is None else mincutoff)
        self.beta = getattr(C, "POSE_ONEEURO_BETA", 0.005) if beta is None else beta

        # The one-euro filter needs the sample rate, and the estimator's rate is
        # not a constant we can assume - it depends on the camera and the
        # machine. So it is measured from arrival times and fed in per sample.
        self._rate_hz = 28.0
        # Live noise estimate, per joint. Measured continuously rather than
        # configured, because the same rig produced elbow noise of 2.5 deg and
        # 7.3 deg on different days - lighting, posture and distance move it by
        # 3x, so any constant baked in here is wrong most of the time.
        self._d1 = {j: 0.0 for j in self.JOINTS}    # EWMA of the difference
        self._d2 = {j: 0.0 for j in self.JOINTS}    # EWMA of its square
        self._prev_raw = {}
        self._baseline = {}
        self._filters = {
            j: JointFilter(self._rate_hz, self.median_n, self.mincutoff,
                           self.beta, mode=self.mode, alpha=self.alpha,
                           max_deg_per_s=getattr(C, "POSE_MAX_RATE_DEG_S", 400.0))
            for j in self.JOINTS
        }

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
        """Median prefilter, then an adaptive low-pass. See filters.py.

        Vision noise is two different problems and one filter cannot handle
        both:

          * landmark JUMPS     - occasional large outliers when the estimator
            briefly mis-locates a joint. A low-pass filter cannot reject these;
            it smears one across several frames, which is worse for control
            than the spike itself because the error then persists. The MEDIAN
            stage discards them outright.
          * continuous jitter  - small, every frame. Handled by the second
            stage, which by default is a one-euro filter rather than a fixed
            exponential: it smooths hard while the arm is still and opens up
            while it moves, instead of compromising between the two.

        Order matters - median first. The one-euro stage reads a large fast
        change as genuine motion and speeds up to follow it, so an outlier
        arriving before the median would be passed through, not rejected.
        """
        now = time.monotonic()
        with self._lock:
            self._raw = joints

            # Track the estimator's actual rate; the one-euro filter's cutoffs
            # are in Hz and so are meaningless without it. Smoothed, because a
            # single late datagram should not distort the filter.
            if self._last_rx:
                dt = now - self._last_rx
                if 0.002 < dt < 1.0:
                    inst = 1.0 / dt
                    self._rate_hz = 0.1 * inst + 0.9 * self._rate_hz

            if self._filtered is None:
                self._filtered = {}
            for j in self.JOINTS:
                self._filtered[j] = self._filters[j](joints[j], self._rate_hz)
                self._track_noise(j, self._filtered[j])

            self._last_rx = now
            self._count += 1

    # ---- live noise estimate ----------------------------------------------
    NOISE_ALPHA = 0.02          # ~50-sample memory: about 2 s at 28 Hz
    NOISE_BASELINE_ALPHA = 0.02  # the "where this joint really is" reference
    NOISE_STILL_DEG_S = 15.0    # above this the joint is moving, not wobbling

    def _track_noise(self, joint, filtered):
        """Estimate the residual wobble the CONTROLLER sees, in degrees.

        Measured on the FILTERED stream, not the raw one. The deadband exists
        to stop the controller reacting to whatever noise survives filtering,
        so the raw figure is the wrong quantity - on real data raw noise was
        5.2 deg where the filtered residual was 2.3 deg, and sizing a deadband
        from the former would have doubled it for no reason.

        Nor can it be measured from successive differences here: the filter
        deliberately correlates neighbouring samples, which collapses the
        difference-based estimate to near zero (0.24 deg against a true 2.8).
        So it is measured as the spread around a much slower baseline of the
        same stream.

        UPDATED ONLY WHILE THE JOINT IS NEARLY STILL. That baseline lags real
        movement, so during a commanded move the residual reflects the lag
        rather than the noise, and feeding that in would widen the deadband
        exactly when the arm is trying to travel - it would stop short of every
        target it was moving toward. Stillness is also when the deadband
        actually governs behaviour, so it is the right time to measure.
        """
        prev = self._prev_raw.get(joint)
        self._prev_raw[joint] = filtered

        b = self._baseline.get(joint)
        ba = self.NOISE_BASELINE_ALPHA
        self._baseline[joint] = filtered if b is None else \
            ba * filtered + (1 - ba) * b
        if b is None or prev is None:
            return

        speed = abs(filtered - prev) * self._rate_hz
        if speed > self.NOISE_STILL_DEG_S:
            return                      # moving: this residual is lag, not noise

        r = filtered - b
        a = self.NOISE_ALPHA
        self._d1[joint] = a * r + (1 - a) * self._d1[joint]
        self._d2[joint] = a * r * r + (1 - a) * self._d2[joint]

    def noise_sd(self, joint=None):
        """Estimated residual noise in degrees. Dict, or one joint's value."""
        with self._lock:
            out = {}
            for j in self.JOINTS:
                var = self._d2[j] - self._d1[j] * self._d1[j]
                out[j] = math.sqrt(max(var, 0.0))
        return out if joint is None else out.get(joint, 0.0)

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
                    "rate_hz": round(self._rate_hz, 1),
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

    def noise_sd(self, joint=None):
        """No measurement, so no measurement noise. Keeps the adaptive
        deadband at its configured floor in simulation, which is what makes
        --sim results comparable with the tuning done on real captures."""
        zero = {j: 0.0 for j in self.joints}
        return zero if joint is None else 0.0

    def stats(self):
        return {"simulated": True}
