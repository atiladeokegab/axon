"""
Exercises backend/main.py through FastAPI's TestClient — real HTTP/WebSocket
handling, mocked driver/pipeline underneath so it doesn't need a camera or
board. Uses the module-level `controller`/`broadcaster` singletons from
backend.main, same as the real app.
"""
import io

import numpy as np
import cv2
import pytest
from fastapi.testclient import TestClient

from live_twin.backend import main


@pytest.fixture(autouse=True)
def avoid_live_camera_runtime(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(main.runtime, "acquire", noop)
    monkeypatch.setattr(main.runtime, "release", noop)
    monkeypatch.setattr(main.runtime, "shutdown", noop)


def test_websocket_sends_ready_message_with_armed_state():
    with TestClient(main.app).websocket_connect("/ws") as ws:
        first = ws.receive_json()

    assert first == {"type": "ready", "armed": False}  # DRIVER_MOCK_MODE defaults on


def test_websocket_select_motion_fires_mock_pad_and_reports_done():
    with TestClient(main.app).websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "select_motion", "motion": "grip"})

        firing = ws.receive_json()
        done = ws.receive_json()

    assert firing == {
        "type": "status", "pad": "WRIST_FLEX", "state": "firing",
        "duration_ms": 800,
    }
    assert done == {"type": "status", "pad": "WRIST_FLEX", "state": "done"}


def test_websocket_select_motion_with_unknown_motion_reports_error():
    with TestClient(main.app).websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "select_motion", "motion": "not_a_real_motion"})

        error = ws.receive_json()

    assert error["type"] == "status"
    assert error["state"] == "error"
    assert "unknown motion" in error["detail"]


def _png_bytes(color=(120, 150, 180)):
    img = np.full((50, 50, 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return io.BytesIO(buf.tobytes())


def test_placement_endpoint_returns_422_on_undecodable_upload():
    files = {
        "relaxed": ("relaxed.png", io.BytesIO(b"not an image"), "image/png"),
        "bicep_flexed": ("b.png", _png_bytes(), "image/png"),
        "tricep_flexed": ("t.png", _png_bytes(), "image/png"),
        "front": ("f.png", _png_bytes(), "image/png"),
        "back": ("bk.png", _png_bytes(), "image/png"),
    }

    response = TestClient(main.app).post("/placement", files=files)

    assert response.status_code == 422
    assert "could not decode" in response.json()["detail"]


def test_placement_endpoint_returns_200_with_calibration_incomplete_when_nothing_detected(
    monkeypatch,
):
    # plain colour images: valid PNGs, but no arm/dots to detect — this
    # should be a normal 200 with calibration_complete=False, not an error.
    files = {
        "relaxed": ("relaxed.png", _png_bytes(), "image/png"),
        "bicep_flexed": ("b.png", _png_bytes(), "image/png"),
        "tricep_flexed": ("t.png", _png_bytes(), "image/png"),
        "front": ("f.png", _png_bytes(), "image/png"),
        "back": ("bk.png", _png_bytes(), "image/png"),
    }

    monkeypatch.setattr(
        main,
        "compute_placement",
        lambda **_photos: {
            "calibration_complete": False,
            "pads": {"wrist": {"ok": False}},
        },
    )

    response = TestClient(main.app).post("/placement", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["calibration_complete"] is False
    assert body["pads"]["wrist"]["ok"] is False
