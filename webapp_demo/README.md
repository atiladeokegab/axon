# Axon

Axon is one FastAPI site containing the original landing page, Muscle Mapper,
and the integrated Live Twin motor-recovery application.

## Run locally

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```powershell
Copy-Item .env.example .env
uv sync --dev
uv run python live_twin/scripts/download_pose_model.py
uv run uvicorn main:app --reload
```

Open:

- `/` — Axon landing page
- `/ems-muscle-mapper/` — Muscle Mapper
- `/live-twin/` — Live Twin

Live Twin exposes its WebSocket at `/live-twin/ws`, its shared camera preview
at `/live-twin/camera.mjpeg`, its five-photo calibration endpoint at
`/live-twin/placement`, and frontend assets beneath `/live-twin/assets/`.

The camera and MediaPipe model start only when the first WebSocket or camera
stream client connects and stop after the final client disconnects. The
placement endpoint does not start that runtime.

## Safety defaults

TENS actuation is mocked by default. Keep `DRIVER_MOCK_MODE=true`,
`BOARD_CONTRACT_CONFIRMED=false`, and `RELAY_PAIRING_CONFIRMED=false` unless
the physical rig, relay pairing, and controller protocol have all been
verified. The separate pose UDP output is also off by default.

## Updating the Live Twin subtree

`live_twin/` was imported from `atiladeokegab/axon` with full history. The
upstream repository remains read-only. To inspect and incorporate a future
upstream update:

```powershell
git fetch live-twin-upstream main
git subtree pull --prefix=live_twin live-twin-upstream main
```

Resolve integration conflicts in this repository, then rerun the complete
test suite. Do not push integration commits to the upstream repository.

The imported application's detailed hardware, placement, and pose protocol
notes remain in [`live_twin/README.md`](live_twin/README.md).
