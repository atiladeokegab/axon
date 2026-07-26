import io

from fastapi.testclient import TestClient

from ems_muscle_mapper import main


def test_all_three_pages_render_with_the_shared_header():
    with TestClient(main.app) as client:
        landing = client.get("/")
        mapper = client.get("/ems-muscle-mapper/")
        twin = client.get("/live-twin/")

    assert landing.status_code == 200
    assert mapper.status_code == 200
    assert twin.status_code == 200
    for response in (landing, mapper, twin):
        assert 'class="site-header"' in response.text
        assert 'href="/live-twin/"' in response.text
        assert 'href="/ems-muscle-mapper/"' in response.text

    assert 'aria-current="page"' not in landing.text
    assert "Muscle Mapper" in mapper.text
    assert 'href="/ems-muscle-mapper/"' in mapper.text
    assert "Live Twin / Session" in twin.text
    assert 'href="/live-twin/"' in twin.text


def test_live_twin_assets_are_served_beneath_the_stable_base():
    client = TestClient(main.app)
    attribution = client.get("/live-twin/assets/THIRD_PARTY_ASSETS.md")
    model = client.get("/live-twin/assets/bp3d/FJ1467.obj")
    voice = client.get("/live-twin/assets/voice/manifest.json")

    assert attribution.status_code == 200
    assert "BodyParts3D" in attribution.text
    assert model.status_code == 200
    assert len(model.content) > 90_000
    assert voice.status_code == 200
    assert voice.json()["begin"]["file"] == "begin.mp3"


def test_live_twin_websocket_uses_mounted_route_without_real_camera(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(main.live_twin_runtime, "acquire", noop)
    monkeypatch.setattr(main.live_twin_runtime, "release", noop)
    monkeypatch.setattr(main.live_twin_runtime, "shutdown", noop)

    with TestClient(main.app).websocket_connect("/live-twin/ws") as websocket:
        assert websocket.receive_json() == {"type": "ready", "armed": False}


def test_placement_route_does_not_acquire_live_runtime(monkeypatch):
    async def fail_if_called():
        raise AssertionError("placement must not start the live runtime")

    async def noop():
        return None

    monkeypatch.setattr(main.live_twin_runtime, "acquire", fail_if_called)
    monkeypatch.setattr(main.live_twin_runtime, "shutdown", noop)
    files = {
        field: (f"{field}.png", io.BytesIO(b"not an image"), "image/png")
        for field in ("relaxed", "bicep_flexed", "tricep_flexed", "front", "back")
    }

    response = TestClient(main.app).post("/live-twin/placement", files=files)

    assert response.status_code == 422
