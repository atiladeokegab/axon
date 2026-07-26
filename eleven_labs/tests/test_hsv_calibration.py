import cv2
import numpy as np

from backend.placement import hsv_calibration


def _solid_bgr_image(bgr_color, size=20):
    return np.full((size, size, 3), bgr_color, dtype=np.uint8)


def test_suggest_hsv_range_centers_on_the_sampled_pixels_hsv():
    image = _solid_bgr_image((180, 0, 200))  # arbitrary magenta-ish BGR
    expected_hsv = cv2.cvtColor(np.uint8([[[180, 0, 200]]]), cv2.COLOR_BGR2HSV)[0, 0]

    lower, upper = hsv_calibration.suggest_hsv_range(image, x=10, y=10, tolerance=(10, 60, 60))

    for i, tol in enumerate((10, 60, 60)):
        assert lower[i] <= int(expected_hsv[i]) <= upper[i]
        assert upper[i] - lower[i] <= 2 * tol  # not wider than the requested window (before clamping)


def test_suggest_hsv_range_clamps_hue_to_valid_opencv_bounds():
    image = _solid_bgr_image((0, 0, 255))  # pure red -> hue near 0
    lower, upper = hsv_calibration.suggest_hsv_range(image, x=5, y=5, tolerance=(20, 60, 60))

    assert lower[0] >= 0
    assert upper[0] <= 179


def test_suggest_hsv_range_clamps_saturation_and_value_to_valid_bounds():
    image = _solid_bgr_image((255, 255, 255))  # white -> low saturation, high value
    lower, upper = hsv_calibration.suggest_hsv_range(image, x=5, y=5, tolerance=(10, 60, 60))

    assert lower[1] >= 0
    assert upper[1] <= 255
    assert lower[2] >= 0
    assert upper[2] <= 255


def test_suggest_hsv_range_clips_patch_to_image_bounds_near_edges():
    image = _solid_bgr_image((140, 90, 90), size=10)

    # sampling right at the corner shouldn't raise/crash despite the patch
    # window extending past the image bounds
    lower, upper = hsv_calibration.suggest_hsv_range(image, x=0, y=0, tolerance=(10, 60, 60))

    assert lower[0] <= upper[0]
