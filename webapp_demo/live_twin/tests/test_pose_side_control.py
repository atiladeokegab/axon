"""
Covers user-chosen arm tracking and the "why is there no pose" reporting.

The estimator's frame loop needs a real MediaPipe model + camera, so these
drive the side/status logic through a fake landmarker instead — the part that
actually encodes the product decisions is _pick_side/read(), not MediaPipe.
"""
import pytest

from live_twin.backend import config
from live_twin.backend.pose import estimator as estimator_module
from live_twin.backend.pose.estimator import (
    LEFT_INDICES,
    RIGHT_INDICES,
    SIDE_LEFT,
    SIDE_RIGHT,
    STATUS_ARM_NOT_VISIBLE,
    STATUS_NO_PERSON,
    STATUS_TRACKING,
    ArmPoseEstimator,
    validate_side,
)
from live_twin.backend.pose.broadcaster import PoseBroadcaster


class _FakeLandmark:
    def __init__(self, x, y, visibility, z=0.0):
        self.x = x
        self.y = y
        self.z = z
        self.visibility = visibility


def _make_landmarks(left_visibility: float, right_visibility: float):
    size = max(idx.value for idx in list(LEFT_INDICES.values()) + list(RIGHT_INDICES.values())) + 1
    landmarks = [_FakeLandmark(0.0, 0.0, 0.0) for _ in range(size)]
    for idx in LEFT_INDICES.values():
        landmarks[idx] = _FakeLandmark(0.2, 0.2, left_visibility, z=0.25)
    for idx in RIGHT_INDICES.values():
        landmarks[idx] = _FakeLandmark(0.8, 0.8, right_visibility, z=-0.35)
    return landmarks


class _FakeResult:
    """
    Mirrors MediaPipe's result object, which carries the same detection twice:
    normalized image coords for the twin, and metric world coords for the
    control loop's UDP feed.
    """

    def __init__(self, pose_landmarks, pose_world_landmarks=None):
        self.pose_landmarks = pose_landmarks
        # Defaults to the same landmarks: these tests only assert side/status
        # selection, which is driven off the normalized set. Metric-specific
        # behaviour is covered in test_control_link.py.
        self.pose_world_landmarks = (
            pose_landmarks if pose_world_landmarks is None else pose_world_landmarks
        )


class _FakeLandmarker:
    """Stands in for the MediaPipe landmarker; returns whatever it's told to."""

    def __init__(self):
        self.result = _FakeResult([])

    def detect_for_video(self, _image, _timestamp_ms):
        return self.result

    def close(self):
        pass


@pytest.fixture
def estimator(monkeypatch):
    """An ArmPoseEstimator whose model and frame conversion are faked out."""
    monkeypatch.setattr(
        ArmPoseEstimator, "__init__",
        lambda self, **kwargs: None,
    )
    inst = ArmPoseEstimator()
    inst._landmarker = _FakeLandmarker()
    inst._start_time = 0.0
    inst._locked_side = None
    inst._forced_side = None
    monkeypatch.setattr(estimator_module.mp, "Image", lambda **kwargs: object())
    return inst


def _read(estimator, left_visibility, right_visibility, person=True):
    estimator._landmarker.result = _FakeResult(
        [_make_landmarks(left_visibility, right_visibility)] if person else []
    )
    return estimator.read(frame_rgb=None)


def test_reports_no_person_when_nothing_is_detected(estimator):
    reading = _read(estimator, 0.9, 0.9, person=False)

    assert reading.status == STATUS_NO_PERSON
    assert reading.landmarks is None


def test_reports_arm_not_visible_when_a_person_is_there_but_no_arm_clears(estimator):
    reading = _read(estimator, 0.1, 0.1)

    # Distinct from no_person: the UI says "get your arm up", not "step in".
    assert reading.status == STATUS_ARM_NOT_VISIBLE
    assert reading.landmarks is None


def test_auto_mode_tracks_whichever_arm_is_visible(estimator):
    reading = _read(estimator, 0.1, 0.9)

    assert reading.status == STATUS_TRACKING
    assert reading.side == SIDE_RIGHT
    assert set(reading.landmarks) == {"shoulder", "elbow", "wrist"}


def test_forced_side_is_tracked_even_when_the_other_arm_is_more_visible(estimator):
    estimator.set_side(SIDE_LEFT)

    reading = _read(estimator, 0.6, 0.99)

    assert reading.side == SIDE_LEFT
    assert reading.status == STATUS_TRACKING


def test_forced_side_never_falls_back_to_the_other_arm(estimator):
    """
    The auto-lock deliberately releases to the other arm when its own dims.
    A user-chosen side must NOT: silently training the wrong arm is a safety
    problem, so it reports the arm as missing instead.
    """
    estimator.set_side(SIDE_LEFT)

    reading = _read(estimator, 0.0, 0.99)

    assert reading.landmarks is None
    assert reading.side == SIDE_LEFT
    assert reading.status == STATUS_ARM_NOT_VISIBLE


def test_switching_side_takes_effect_immediately_despite_an_existing_auto_lock(estimator):
    # auto-lock onto left first
    assert _read(estimator, 0.9, 0.1).side == SIDE_LEFT

    estimator.set_side(SIDE_RIGHT)
    reading = _read(estimator, 0.9, 0.9)

    assert reading.side == SIDE_RIGHT


def test_returning_to_auto_lets_the_other_arm_be_picked_again(estimator):
    estimator.set_side(SIDE_LEFT)
    estimator.set_side(None)

    reading = _read(estimator, 0.1, 0.9)

    assert reading.side == SIDE_RIGHT
    assert reading.status == STATUS_TRACKING


def test_process_still_returns_bare_landmarks_for_existing_callers(estimator):
    estimator._landmarker.result = _FakeResult([_make_landmarks(0.9, 0.1)])

    assert set(estimator.process(frame_rgb=None)) == {"shoulder", "elbow", "wrist"}

    estimator._landmarker.result = _FakeResult([])
    assert estimator.process(frame_rgb=None) is None


@pytest.mark.parametrize("bad", ["LEFT", "Right", "both", "", 0])
def test_invalid_sides_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_side(bad)


def test_broadcaster_remembers_the_side_across_a_restart():
    """
    start() builds a fresh estimator, so a side held only on the estimator
    would silently revert to auto after any stop()/start() cycle.
    """
    broadcaster = PoseBroadcaster()
    broadcaster.set_side(SIDE_LEFT)

    assert broadcaster.side == SIDE_LEFT


def test_broadcaster_rejects_an_invalid_side_without_storing_it():
    broadcaster = PoseBroadcaster()
    broadcaster.set_side(SIDE_RIGHT)

    with pytest.raises(ValueError):
        broadcaster.set_side("sideways")

    assert broadcaster.side == SIDE_RIGHT


def test_min_visibility_threshold_is_the_one_from_config(estimator):
    just_under = config.MIN_LANDMARK_VISIBILITY - 0.01
    just_over = config.MIN_LANDMARK_VISIBILITY + 0.01

    assert _read(estimator, just_under, just_under).status == STATUS_ARM_NOT_VISIBLE
    assert _read(estimator, just_over, 0.0).status == STATUS_TRACKING
