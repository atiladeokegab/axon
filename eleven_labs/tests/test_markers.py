import cv2
import numpy as np
import pytest

from backend import config
from backend.placement import markers


def _dot_image(shape=(200, 200), dot_center=None, dot_radius=6):
    img = np.full((*shape, 3), (120, 150, 180), dtype=np.uint8)
    if dot_center is not None:
        hsv_dot = np.uint8([[[155, 200, 200]]])
        bgr_dot = tuple(int(c) for c in cv2.cvtColor(hsv_dot, cv2.COLOR_HSV2BGR)[0, 0])
        cv2.circle(img, dot_center, dot_radius, bgr_dot, -1)
    return img


def test_locate_muscle_flex_point_finds_the_dot_that_moved():
    relaxed = _dot_image(dot_center=(100, 100))
    flexed = _dot_image(dot_center=(100, 80))  # moved 20px up

    point, displacement = markers.locate_muscle_flex_point(relaxed, flexed)

    assert displacement >= config.MARKER_MIN_FLEX_DISPLACEMENT_PX
    assert abs(point[1] - 80) < 3  # tracked to roughly the new position


def test_locate_muscle_flex_point_raises_on_mismatched_image_sizes():
    relaxed = _dot_image(shape=(200, 200), dot_center=(100, 100))
    flexed = _dot_image(shape=(150, 150), dot_center=(80, 80))

    with pytest.raises(ValueError, match="size mismatch"):
        markers.locate_muscle_flex_point(relaxed, flexed)


def test_locate_muscle_flex_point_raises_when_no_dots_present():
    relaxed = _dot_image(dot_center=None)  # no dot at all
    flexed = _dot_image(dot_center=None)

    with pytest.raises(ValueError, match="no marker dots"):
        markers.locate_muscle_flex_point(relaxed, flexed)


def test_locate_muscle_flex_point_raises_when_dot_barely_moves():
    relaxed = _dot_image(dot_center=(100, 100))
    flexed = _dot_image(dot_center=(101, 100))  # 1px — camera noise, not a real flex

    with pytest.raises(ValueError, match="did not move|no marker moved"):
        markers.locate_muscle_flex_point(relaxed, flexed)


def test_detect_dots_filters_out_noise_sized_contours():
    img = _dot_image(dot_center=None)
    hsv_dot = np.uint8([[[155, 200, 200]]])
    bgr_dot = tuple(int(c) for c in cv2.cvtColor(hsv_dot, cv2.COLOR_HSV2BGR)[0, 0])
    # a single stray pixel of marker colour — too small to be a real dot
    img[50, 50] = bgr_dot

    dots = markers.detect_dots(img)

    assert len(dots) == 0
