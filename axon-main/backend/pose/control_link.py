"""
Pose feed to the teammates' control loop, per POSE_API.md.

Deliberately separate from the WebSocket the twin UI consumes, because the two
have opposite requirements when tracking is lost:

- the UI needs to be *told* the arm is missing, so it can prompt the user
  ("get your arm in the camera") instead of freezing on a stale pose;
- this contract requires *silence*, because silence is what stops their
  stimulation. Requirement 2: "Do not send a guess ... a fabricated pose makes
  the controller drive the limb against reality."

So this module only ever emits complete, currently-tracked poses. There is no
code path here that sends a fallback, a last-known value, or an interpolation.

UDP is their choice, on purpose: they want the newest pose, never a replayed
backlog of stale ones, and a dropped packet is harmless.
"""
import json
import logging
import socket
import time

from backend import config

logger = logging.getLogger("pose_control_link")

JOINTS = ("shoulder", "elbow", "wrist")


def to_control_frame(point) -> list[float]:
    """
    MediaPipe world landmarks -> the control loop's axes.

    MediaPipe world landmarks are metres with the origin at the hip midpoint and
    axes aligned to the image: +x image-right, +y down, +z away from the camera.
    A subject facing the camera therefore has their LEFT on image-right.

    POSE_API.md wants a right-handed subject-centred frame:
        +X = subject's forward   = toward the camera = -z
        +Y = subject's left      = image-right       = +x
        +Z = up                  = image-up          = -y

    That mapping is a pure rotation (determinant +1, orthonormal), so it
    preserves handedness and introduces no scale or skew — verified in
    tests/test_control_link.py, which also pins the sign of each axis against
    four physical poses. Getting this wrong does not fail loudly; it drives the
    limb the wrong way, which is why the signs are tested rather than trusted.
    """
    x, y, z = point
    return [-z, x, -y]


class PoseControlLink:
    """Fire-and-forget UDP sender. Never raises into the capture loop."""

    def __init__(self, host: str | None = None, port: int | None = None,
                 enabled: bool | None = None):
        self.host = config.POSE_UDP_HOST if host is None else host
        self.port = config.POSE_UDP_PORT if port is None else port
        self.enabled = config.POSE_UDP_ENABLED if enabled is None else enabled
        self._socket: socket.socket | None = None
        self._oversize_logged = False
        self._send_failure_logged = False

    def _ensure_socket(self) -> socket.socket:
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return self._socket

    def build_payload(self, world_landmarks: dict, timestamp: float | None = None) -> dict:
        payload = {
            joint: [round(v, 5) for v in to_control_frame(world_landmarks[joint])]
            for joint in JOINTS
        }
        # Required: it is how they detect staleness (>300ms stops stimulation).
        payload["timestamp"] = time.time() if timestamp is None else timestamp
        return payload

    def send(self, reading) -> bool:
        """
        Emit one pose. Returns True only if a datagram actually went out.

        Silently does nothing unless the frame is a complete, tracked pose with
        metric landmarks — that silence is the safety behaviour, not an
        oversight. Callers must not "helpfully" retry or substitute a value.
        """
        if not self.enabled:
            return False
        if reading.status != "tracking" or not reading.world_landmarks:
            return False
        if any(joint not in reading.world_landmarks for joint in JOINTS):
            return False

        try:
            payload = self.build_payload(reading.world_landmarks)
            datagram = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError, KeyError):
            logger.exception("could not encode pose for the control loop; dropping frame")
            return False

        if len(datagram) > config.POSE_UDP_MAX_DATAGRAM_BYTES:
            # Dropped rather than truncated: a half-message is a malformed pose,
            # and their receiver counts-and-drops those anyway.
            if not self._oversize_logged:
                logger.error(
                    "pose datagram %d bytes exceeds the %d-byte contract limit — dropping",
                    len(datagram), config.POSE_UDP_MAX_DATAGRAM_BYTES,
                )
                self._oversize_logged = True
            return False

        try:
            self._ensure_socket().sendto(datagram, (self.host, self.port))
        except OSError:
            # Never let a network problem break the capture loop or the UI feed.
            # Logged once so a persistent misconfiguration is visible without
            # flooding at 16Hz.
            if not self._send_failure_logged:
                logger.exception("pose UDP send to %s:%s failed", self.host, self.port)
                self._send_failure_logged = True
            return False

        self._send_failure_logged = False
        return True

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
