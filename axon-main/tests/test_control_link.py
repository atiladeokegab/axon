"""
Covers the pose feed to the teammates' control loop (POSE_API.md).

Two things here are safety properties, not style preferences:
- the coordinate mapping, because wrong axes don't fail loudly, they drive the
  limb the wrong way;
- silence when tracking is lost, because silence is what stops stimulation.
"""
import json
import socket

import pytest

from backend import config
from backend.pose.control_link import JOINTS, PoseControlLink, to_control_frame
from backend.pose.estimator import (
    STATUS_ARM_NOT_VISIBLE,
    STATUS_NO_PERSON,
    STATUS_TRACKING,
    PoseReading,
)


def _world(shoulder=(0.0, 0.0, 0.0), elbow=(0.0, 0.3, 0.0), wrist=(0.0, 0.55, 0.0)):
    return {"shoulder": shoulder, "elbow": elbow, "wrist": wrist}


def _tracked(world=None):
    return PoseReading(
        landmarks={"shoulder": (0, 0, 0), "elbow": (0, 0, 0), "wrist": (0, 0, 0)},
        side="right",
        status=STATUS_TRACKING,
        world_landmarks=_world() if world is None else world,
    )


class _FakeSocket:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple]] = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


@pytest.fixture
def link(monkeypatch):
    """An enabled link with its UDP socket replaced by a recorder."""
    link = PoseControlLink(host="127.0.0.1", port=9090, enabled=True)
    fake = _FakeSocket()
    monkeypatch.setattr(link, "_ensure_socket", lambda: fake)
    link.fake = fake
    return link


# --- coordinate frame -------------------------------------------------------

@pytest.mark.parametrize("mediapipe_point,expected,description", [
    ((0.0, 0.5, 0.0), (0.0, 0.0, -0.5), "arm hanging down -> Z negative (down)"),
    ((0.0, -0.5, 0.0), (0.0, 0.0, 0.5), "arm overhead -> Z positive (up)"),
    ((0.0, 0.0, -0.5), (0.5, 0.0, 0.0), "arm toward camera -> X positive (forward)"),
    ((0.5, 0.0, 0.0), (0.0, 0.5, 0.0), "image-right -> Y positive (subject's left)"),
])
def test_axis_signs_match_the_contract(mediapipe_point, expected, description):
    assert tuple(to_control_frame(mediapipe_point)) == pytest.approx(expected), description


def test_mapping_is_a_pure_rotation():
    """
    Must preserve handedness and introduce no scale: the contract's frame is
    right-handed, and a reflection here would mirror the limb's motion.
    """
    basis = [to_control_frame(v) for v in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]]
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = basis

    determinant = (
        ax * (by * cz - bz * cy)
        - ay * (bx * cz - bz * cx)
        + az * (bx * cy - by * cx)
    )
    assert determinant == pytest.approx(1.0), "not a rotation — handedness flipped or scaled"

    for vector in basis:
        assert sum(component ** 2 for component in vector) == pytest.approx(1.0)


def test_relative_joint_geometry_survives_the_conversion():
    """Only relative vectors matter to them, so distances must be preserved."""
    shoulder = to_control_frame((0.1, 0.0, 0.0))
    wrist = to_control_frame((0.1, 0.5, 0.0))

    separation = sum((a - b) ** 2 for a, b in zip(shoulder, wrist)) ** 0.5
    assert separation == pytest.approx(0.5)


# --- silence when tracking is lost ------------------------------------------

@pytest.mark.parametrize("status", [STATUS_NO_PERSON, STATUS_ARM_NOT_VISIBLE])
def test_sends_nothing_when_tracking_is_lost(link, status):
    """
    POSE_API.md req 2: silence safely stops stimulation, a fabricated pose makes
    the controller drive the limb against reality.
    """
    lost = PoseReading(None, "right", status, None)

    assert link.send(lost) is False
    assert link.fake.sent == []


def test_sends_nothing_when_metric_landmarks_are_missing(link):
    """
    A tracked frame with no world landmarks still must not be sent: the
    normalized coords we do have carry no scale, so they would be wrong in
    metres, not merely imprecise.
    """
    no_metric = PoseReading(
        landmarks={"shoulder": (0, 0, 0), "elbow": (0, 0, 0), "wrist": (0, 0, 0)},
        side="right",
        status=STATUS_TRACKING,
        world_landmarks=None,
    )

    assert link.send(no_metric) is False
    assert link.fake.sent == []


def test_sends_nothing_when_a_joint_is_missing(link):
    partial = _tracked(world={"shoulder": (0.0, 0.0, 0.0), "elbow": (0.0, 0.3, 0.0)})

    assert link.send(partial) is False
    assert link.fake.sent == []


def test_disabled_link_sends_nothing(link):
    link.enabled = False

    assert link.send(_tracked()) is False
    assert link.fake.sent == []


# --- message shape ----------------------------------------------------------

def test_tracked_pose_is_sent_to_the_configured_endpoint(link):
    assert link.send(_tracked()) is True
    assert len(link.fake.sent) == 1

    data, addr = link.fake.sent[0]
    assert addr == ("127.0.0.1", 9090)

    payload = json.loads(data.decode("utf-8"))
    assert set(payload) == {*JOINTS, "timestamp"}
    for joint in JOINTS:
        assert len(payload[joint]) == 3
        assert all(isinstance(v, (int, float)) for v in payload[joint])


def test_every_message_carries_a_timestamp(link):
    """It is how they detect staleness; without it nothing ever goes stale."""
    link.send(_tracked())

    payload = json.loads(link.fake.sent[0][0].decode("utf-8"))
    assert isinstance(payload["timestamp"], float)
    assert payload["timestamp"] > 0


def test_datagram_stays_inside_the_contract_size_limit(link):
    link.send(_tracked())

    assert len(link.fake.sent[0][0]) < config.POSE_UDP_MAX_DATAGRAM_BYTES


def test_one_complete_json_object_per_datagram(link):
    """Their receiver parses one message per datagram; framing must not drift."""
    link.send(_tracked())

    data = link.fake.sent[0][0]
    assert data.startswith(b"{") and data.endswith(b"}")
    assert b"\n" not in data
    json.loads(data)  # parses whole, not just a prefix


# --- robustness -------------------------------------------------------------

def test_send_failure_never_escapes_into_the_capture_loop(link, monkeypatch):
    """A network problem must not break pose capture or the UI's feed."""
    class _Broken:
        def sendto(self, *_):
            raise OSError("network unreachable")

    monkeypatch.setattr(link, "_ensure_socket", lambda: _Broken())

    assert link.send(_tracked()) is False  # reported, not raised


def test_defaults_come_from_config_and_are_off_unless_enabled():
    """
    Enabling this starts driving a limb on their rig, so it must not be on by
    accident.
    """
    assert config.POSE_UDP_ENABLED is False
    assert config.POSE_UDP_PORT == 9090

    assert PoseControlLink().enabled is False


def test_close_releases_the_socket():
    link = PoseControlLink(enabled=True)
    fake = _FakeSocket()
    link._socket = fake

    link.close()

    assert fake.closed is True
    assert link._socket is None
