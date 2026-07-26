import base64

import cv2
import numpy as np

from live_twin.backend.placement import overlay


def _blank(color=(120, 150, 180)) -> np.ndarray:
    return np.full((200, 300, 3), color, dtype=np.uint8)


def test_draw_pad_marker_does_not_mutate_original():
    image = _blank()
    original = image.copy()

    overlay.draw_pad_marker(image, {"x": 0.5, "y": 0.5}, "WRIST")

    assert np.array_equal(image, original)


def test_draw_pad_marker_returns_same_shape():
    image = _blank()

    annotated = overlay.draw_pad_marker(image, {"x": 0.5, "y": 0.5}, "WRIST")

    assert annotated.shape == image.shape


def test_draw_pad_marker_changes_pixels_near_the_normalized_point():
    image = _blank()
    h, w = image.shape[:2]
    cx, cy = int(0.5 * w), int(0.5 * h)

    annotated = overlay.draw_pad_marker(image, {"x": 0.5, "y": 0.5}, "WRIST")

    region = annotated[cy - 3:cy + 3, cx - 3:cx + 3]
    original_region = image[cy - 3:cy + 3, cx - 3:cx + 3]
    assert not np.array_equal(region, original_region)


def test_draw_pad_marker_leaves_far_pixels_untouched():
    image = _blank()

    annotated = overlay.draw_pad_marker(image, {"x": 0.1, "y": 0.1}, "WRIST")

    corner = annotated[180:200, 280:300]
    original_corner = image[180:200, 280:300]
    assert np.array_equal(corner, original_corner)


def test_encode_png_b64_roundtrips_to_a_decodable_image_of_the_same_size():
    image = _blank()

    encoded = overlay.encode_png_b64(image)

    raw = base64.b64decode(encoded)
    arr = np.frombuffer(raw, dtype=np.uint8)
    decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    assert decoded.shape == image.shape
