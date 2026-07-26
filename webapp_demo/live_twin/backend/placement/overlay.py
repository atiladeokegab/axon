"""
Draws the pad-placement guidance the brief asks for directly onto the
calibration photo, rather than leaving the raw {x, y} from pipeline.py as
numbers a frontend has to render itself.
"""
import base64

import cv2
import numpy as np

MARKER_COLOR_BGR = (0, 0, 255)
MARKER_RADIUS_PX = 10
MARKER_CENTER_RADIUS_PX = 2
MARKER_RING_THICKNESS_PX = 2
LABEL_OFFSET_PX = (14, -14)
LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
LABEL_SCALE = 0.6
LABEL_THICKNESS = 2


def draw_pad_marker(image_bgr: np.ndarray, point_norm: dict, label: str) -> np.ndarray:
    """
    Returns a copy of image_bgr with a labeled ring-and-dot marker drawn at
    the normalized (x, y) point (same coordinate space pipeline.py already
    returns). Original array is untouched.
    """
    h, w = image_bgr.shape[:2]
    cx = int(round(point_norm["x"] * w))
    cy = int(round(point_norm["y"] * h))

    annotated = image_bgr.copy()
    cv2.circle(annotated, (cx, cy), MARKER_RADIUS_PX, MARKER_COLOR_BGR, MARKER_RING_THICKNESS_PX)
    cv2.circle(annotated, (cx, cy), MARKER_CENTER_RADIUS_PX, MARKER_COLOR_BGR, -1)
    text_pos = (cx + LABEL_OFFSET_PX[0], cy + LABEL_OFFSET_PX[1])
    cv2.putText(annotated, label, text_pos, LABEL_FONT, LABEL_SCALE,
                MARKER_COLOR_BGR, LABEL_THICKNESS, cv2.LINE_AA)
    return annotated


def encode_png_b64(image_bgr: np.ndarray) -> str:
    """PNG-encodes an image and returns it as a base64 ascii string, ready
    to drop straight into a JSON response."""
    ok, buf = cv2.imencode(".png", image_bgr)
    if not ok:
        raise ValueError("failed to PNG-encode overlay image")
    return base64.b64encode(buf.tobytes()).decode("ascii")
