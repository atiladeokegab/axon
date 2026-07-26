from backend.placement.geometry import Point, wrist_pad_point, delt_pad_point
from backend import config


def test_wrist_pad_point_is_between_wrist_and_elbow():
    wrist = Point(0.0, 0.0)
    elbow = Point(1.0, 0.0)

    pad = wrist_pad_point(wrist, elbow)

    assert pad.x == config.WRIST_PAD_OFFSET_PCT
    assert pad.y == 0.0


def test_delt_pad_point_moves_from_shoulder_toward_elbow():
    shoulder = Point(0.0, 0.0)
    elbow = Point(0.0, 1.0)

    pad = delt_pad_point(shoulder, elbow)

    assert pad.y == config.DELT_PAD_OFFSET_PCT
