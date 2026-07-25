"""
Suggests MARKER_HSV_LOWER/MARKER_HSV_UPPER values from a real photo of the
tape actually in use, instead of guessing bounds blind (see config.py's
placeholder pink/magenta default). Samples a small neighborhood around a
point you pick by eye in any image viewer and widens by a tolerance per HSV
channel, clamped to OpenCV's valid H:[0,179] S:[0,255] V:[0,255] ranges.

Used by scripts/calibrate_tape_color.py.
"""
import cv2
import numpy as np

H_MAX = 179
SV_MAX = 255
DEFAULT_TOLERANCE = (10, 60, 60)
DEFAULT_PATCH_RADIUS_PX = 3


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def suggest_hsv_range(image_bgr: np.ndarray, x: int, y: int,
                       tolerance: tuple = DEFAULT_TOLERANCE,
                       patch_radius: int = DEFAULT_PATCH_RADIUS_PX) -> tuple:
    """
    Returns (lower, upper) HSV tuples suitable for MARKER_HSV_LOWER/UPPER,
    centered on the average color of a small square patch around (x, y) —
    averaging over a patch rather than one pixel is more robust to camera
    noise/compression artifacts on a single sampled pixel.
    """
    h, w = image_bgr.shape[:2]
    x0, x1 = max(0, x - patch_radius), min(w, x + patch_radius + 1)
    y0, y1 = max(0, y - patch_radius), min(h, y + patch_radius + 1)

    patch_bgr = image_bgr[y0:y1, x0:x1]
    patch_hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    mean_h, mean_s, mean_v = patch_hsv.reshape(-1, 3).mean(axis=0)

    tol_h, tol_s, tol_v = tolerance
    lower = (
        _clamp(int(round(mean_h - tol_h)), 0, H_MAX),
        _clamp(int(round(mean_s - tol_s)), 0, SV_MAX),
        _clamp(int(round(mean_v - tol_v)), 0, SV_MAX),
    )
    upper = (
        _clamp(int(round(mean_h + tol_h)), 0, H_MAX),
        _clamp(int(round(mean_s + tol_s)), 0, SV_MAX),
        _clamp(int(round(mean_v + tol_v)), 0, SV_MAX),
    )
    return lower, upper
