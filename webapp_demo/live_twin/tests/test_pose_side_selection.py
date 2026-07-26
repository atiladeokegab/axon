"""
Tests the side-locking logic in backend/pose/estimator.py directly against
fake landmark data, so this doesn't need the downloaded MediaPipe model file
or a real camera to run.
"""
from live_twin.backend import config
from live_twin.backend.pose.estimator import (
    LEFT_INDICES,
    RIGHT_INDICES,
    SIDE_LEFT,
    SIDE_RIGHT,
    _extract,
    _pick_side,
)


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


def test_extract_preserves_xyz_coordinates():
    landmarks = _make_landmarks(left_visibility=0.9, right_visibility=0.1)

    extracted = _extract(landmarks, LEFT_INDICES)

    assert extracted == {
        'shoulder': (0.2, 0.2, 0.25),
        'elbow': (0.2, 0.2, 0.25),
        'wrist': (0.2, 0.2, 0.25),
    }


def test_returns_none_when_neither_side_is_confidently_visible():
    landmarks = _make_landmarks(left_visibility=0.1, right_visibility=0.2)

    assert _pick_side(landmarks) is None


def test_picks_the_only_confidently_visible_side():
    landmarks = _make_landmarks(left_visibility=0.9, right_visibility=0.1)

    side, visibility = _pick_side(landmarks)

    assert side == SIDE_LEFT
    assert visibility >= config.MIN_LANDMARK_VISIBILITY


def test_picks_the_more_visible_side_when_both_clear_threshold():
    landmarks = _make_landmarks(left_visibility=0.9, right_visibility=0.6)

    side, _ = _pick_side(landmarks)

    assert side == SIDE_LEFT


def test_locked_side_is_kept_even_if_the_other_side_is_now_more_visible():
    # both sides visible, right is higher — but caller has locked onto left
    # from a previous frame, so it should stay on left rather than flip.
    landmarks = _make_landmarks(left_visibility=0.6, right_visibility=0.9)

    side, _ = _pick_side(landmarks, preferred=SIDE_LEFT)

    assert side == SIDE_LEFT


def test_locked_side_releases_once_it_drops_below_threshold():
    landmarks = _make_landmarks(left_visibility=0.1, right_visibility=0.9)

    side, _ = _pick_side(landmarks, preferred=SIDE_LEFT)

    assert side == SIDE_RIGHT
