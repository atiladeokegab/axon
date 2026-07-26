"""
Joint-percentage placement for pads that don't need tape-dot tracking
(wrist, front/rear delt). See markers.py for bicep/tricep, which use
flex/relax displacement instead because isometric flexion doesn't move
the joint landmarks.
"""
from dataclasses import dataclass

from backend import config


@dataclass
class Point:
    x: float
    y: float


def _lerp(a: Point, b: Point, t: float) -> Point:
    return Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)


def wrist_pad_point(wrist: Point, elbow: Point) -> Point:
    """Pad sits a fraction of the forearm length up from the wrist, toward the elbow."""
    return _lerp(wrist, elbow, config.WRIST_PAD_OFFSET_PCT)


def delt_pad_point(shoulder: Point, elbow: Point) -> Point:
    """
    Pad sits a fraction of the upper-arm length down from the shoulder.

    Front vs. rear delt is NOT a parameter here — a single 2D landmark set
    has no front/back depth, so there's no reliable way to pick anterior vs
    posterior from one photo's x/y coordinates. Instead, call this once on
    landmarks from a front-on photo (-> front delt point) and once on
    landmarks from a back-on photo (-> rear delt point). See the placement
    pipeline in README / pipeline.py.
    """
    return _lerp(shoulder, elbow, config.DELT_PAD_OFFSET_PCT)
