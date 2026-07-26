"""
Tests the pipeline's assembly logic (wiring pose + geometry + markers
together) without depending on the downloaded MediaPipe model file, so this
runs in a fresh checkout before anyone's run download_pose_model.py.
"""
import base64

import cv2
import numpy as np

from live_twin.backend.placement import pipeline
from live_twin.backend.pose.estimator import SIDE_LEFT


def _encode(image_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image_bgr)
    assert ok
    return buf.tobytes()


def _dot_image(dot_center) -> np.ndarray:
    img = np.full((200, 200, 3), (120, 150, 180), dtype=np.uint8)
    hsv_dot = np.uint8([[[155, 200, 200]]])
    bgr_dot = tuple(int(c) for c in cv2.cvtColor(hsv_dot, cv2.COLOR_HSV2BGR)[0, 0])
    cv2.circle(img, dot_center, 6, bgr_dot, -1)
    return img


FAKE_LANDMARKS = {
    "shoulder": (0.6, 0.6),
    "elbow": (0.4, 0.6),
    "wrist": (0.2, 0.6),
}


def test_compute_placement_assembles_all_pads_when_everything_detects(monkeypatch):
    monkeypatch.setattr(
        pipeline, "detect_pose_in_image",
        lambda rgb, side=None: (dict(FAKE_LANDMARKS), SIDE_LEFT),
    )

    relaxed = _dot_image((100, 100))
    bicep_flexed = _dot_image((100, 80))
    tricep_flexed = _dot_image((100, 130))

    result = pipeline.compute_placement(
        relaxed=_encode(relaxed),
        bicep_flexed=_encode(bicep_flexed),
        tricep_flexed=_encode(tricep_flexed),
        front=_encode(relaxed),
        back=_encode(relaxed),
    )

    assert result["calibration_complete"] is True
    pads = result["pads"]
    assert set(pads.keys()) == {"wrist", "bicep", "tricep", "front_delt", "rear_delt"}
    for pad in pads.values():
        assert pad["ok"] is True
        assert pad["point"] is not None
        assert pad["detail"] is None
        assert pad["overlay_b64"] is not None
    assert pads["bicep"]["displacement_px"] > 0
    assert pads["tricep"]["displacement_px"] > 0
    assert pads["front_delt"]["point"] == pads["rear_delt"]["point"]  # same fake landmarks fed to both


def test_compute_placement_overlay_is_drawn_on_the_pads_own_source_photo(monkeypatch):
    monkeypatch.setattr(
        pipeline, "detect_pose_in_image",
        lambda rgb, side=None: (dict(FAKE_LANDMARKS), SIDE_LEFT),
    )

    relaxed = _dot_image((100, 100))
    bicep_flexed = _dot_image((100, 80))
    tricep_flexed = _dot_image((100, 130))

    result = pipeline.compute_placement(
        relaxed=_encode(relaxed),
        bicep_flexed=_encode(bicep_flexed),
        tricep_flexed=_encode(tricep_flexed),
        front=_encode(relaxed),
        back=_encode(relaxed),
    )

    for pad_name in ("wrist", "bicep", "tricep", "front_delt", "rear_delt"):
        raw = base64.b64decode(result["pads"][pad_name]["overlay_b64"])
        arr = np.frombuffer(raw, dtype=np.uint8)
        decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        assert decoded.shape == relaxed.shape
        assert not np.array_equal(decoded, relaxed)  # marker was actually drawn


def test_compute_placement_marks_pads_failed_without_raising_when_no_pose_detected(monkeypatch):
    monkeypatch.setattr(pipeline, "detect_pose_in_image", lambda rgb, side=None: None)

    blank = np.zeros((200, 200, 3), dtype=np.uint8)

    result = pipeline.compute_placement(
        relaxed=_encode(blank),
        bicep_flexed=_encode(blank),
        tricep_flexed=_encode(blank),
        front=_encode(blank),
        back=_encode(blank),
    )

    assert result["calibration_complete"] is False
    pads = result["pads"]
    assert pads["wrist"]["ok"] is False
    assert "no arm confidently detected" in pads["wrist"]["detail"]
    assert pads["wrist"]["overlay_b64"] is None
    # front/back never even attempted a detection since there's no locked side to trust
    assert pads["front_delt"]["ok"] is False
    assert "no reference arm" in pads["front_delt"]["detail"]
    assert pads["rear_delt"]["ok"] is False


def test_compute_placement_marks_bicep_tricep_failed_without_raising_when_no_dots(monkeypatch):
    monkeypatch.setattr(
        pipeline, "detect_pose_in_image",
        lambda rgb, side=None: (dict(FAKE_LANDMARKS), SIDE_LEFT),
    )

    no_dots = np.full((200, 200, 3), (120, 150, 180), dtype=np.uint8)

    result = pipeline.compute_placement(
        relaxed=_encode(no_dots),
        bicep_flexed=_encode(no_dots),
        tricep_flexed=_encode(no_dots),
        front=_encode(no_dots),
        back=_encode(no_dots),
    )

    assert result["calibration_complete"] is False
    assert result["pads"]["bicep"]["ok"] is False
    assert result["pads"]["tricep"]["ok"] is False
    # wrist/delts still succeed independently since they don't need dots
    assert result["pads"]["wrist"]["ok"] is True
    assert result["pads"]["front_delt"]["ok"] is True


def test_compute_placement_raises_placement_error_on_undecodable_image():
    good = _encode(_dot_image((100, 100)))

    import pytest
    from live_twin.backend.placement.pipeline import PlacementError

    with pytest.raises(PlacementError, match="could not decode"):
        pipeline.compute_placement(
            relaxed=b"not an image",
            bicep_flexed=good,
            tricep_flexed=good,
            front=good,
            back=good,
        )
