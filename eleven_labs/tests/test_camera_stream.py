from fastapi.testclient import TestClient

from backend import main


def test_camera_stream_wraps_shared_broadcaster_frames_as_mjpeg(monkeypatch):
    jpeg = b"\xff\xd8shared-camera-frame\xff\xd9"

    async def one_frame():
        yield jpeg

    monkeypatch.setattr(main.broadcaster, "frames", one_frame)

    response = TestClient(main.app).get("/camera.mjpeg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "multipart/x-mixed-replace; boundary=frame"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.content == (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
        + jpeg
        + b"\r\n"
    )
