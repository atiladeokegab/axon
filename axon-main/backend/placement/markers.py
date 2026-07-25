"""
Tape-dot marker detection + Lucas-Kanade optical flow for locating the
bicep/tricep flex point. Isometric flexion doesn't move the elbow/shoulder
landmarks, so joint geometry alone can't tell which muscle fired — dots
placed along the muscle belly do, by tracking which one moved the most
between the relaxed and flexed calibration photos.

Camera and arm position must stay fixed between the two shots; only the
muscle should change. All failure modes below raise ValueError with a
specific reason rather than returning a low-confidence guess.
"""
import cv2
import numpy as np

from backend import config


def detect_dots(image_bgr: np.ndarray) -> np.ndarray:
    """Return an (N, 2) array of marker centroids (x, y) in pixel coords,
    filtered to plausible dot-sized contours so stray noise/skin-toned
    blobs don't get mistaken for a marker."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, config.MARKER_HSV_LOWER, config.MARKER_HSV_UPPER)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centroids = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < config.MARKER_MIN_DOT_AREA_PX or area > config.MARKER_MAX_DOT_AREA_PX:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        centroids.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))

    return np.array(centroids, dtype=np.float32)


def track_flex_point(relaxed_gray: np.ndarray, flexed_gray: np.ndarray,
                      relaxed_dots: np.ndarray) -> tuple:
    """
    Track each relaxed-frame dot into the flexed frame via Lucas-Kanade
    optical flow, and return (point, displacement) for the dot that moved
    the most — that's the muscle's bulge peak, i.e. the pad target.
    """
    if relaxed_gray.shape != flexed_gray.shape:
        raise ValueError(
            f"relaxed/flexed frame size mismatch {relaxed_gray.shape} vs "
            f"{flexed_gray.shape} — camera must not move/change resolution between the two shots"
        )
    if len(relaxed_dots) == 0:
        raise ValueError("no marker dots detected in the relaxed-state image")

    pts0 = relaxed_dots.reshape(-1, 1, 2)
    try:
        pts1, status, _ = cv2.calcOpticalFlowPyrLK(relaxed_gray, flexed_gray, pts0, None)
    except cv2.error as exc:
        raise ValueError(f"optical flow failed: {exc}") from exc

    h, w = flexed_gray.shape[:2]
    pts1_flat = pts1.reshape(-1, 2)
    in_bounds = (pts1_flat[:, 0] >= 0) & (pts1_flat[:, 0] < w) & (pts1_flat[:, 1] >= 0) & (pts1_flat[:, 1] < h)

    displacements = np.linalg.norm((pts1 - pts0).reshape(-1, 2), axis=1)
    displacements[status.reshape(-1) == 0] = -1  # dropped by optical flow
    displacements[~in_bounds] = -1                # tracked off-frame, not trustworthy

    best_idx = int(np.argmax(displacements))
    best_displacement = float(displacements[best_idx])

    if best_displacement < config.MARKER_MIN_FLEX_DISPLACEMENT_PX:
        raise ValueError(
            f"no marker moved enough to be confident of a flex "
            f"(best displacement {max(best_displacement, 0):.1f}px, "
            f"need >= {config.MARKER_MIN_FLEX_DISPLACEMENT_PX}px)"
        )

    best_point = tuple(pts1_flat[best_idx])
    return best_point, best_displacement


def locate_muscle_flex_point(relaxed_bgr: np.ndarray, flexed_bgr: np.ndarray) -> tuple:
    """End-to-end: detect dots in the relaxed frame, track into the flexed frame."""
    if relaxed_bgr.shape != flexed_bgr.shape:
        raise ValueError(
            f"relaxed/flexed image size mismatch {relaxed_bgr.shape} vs {flexed_bgr.shape}"
        )

    relaxed_gray = cv2.cvtColor(relaxed_bgr, cv2.COLOR_BGR2GRAY)
    flexed_gray = cv2.cvtColor(flexed_bgr, cv2.COLOR_BGR2GRAY)

    relaxed_dots = detect_dots(relaxed_bgr)
    return track_flex_point(relaxed_gray, flexed_gray, relaxed_dots)
