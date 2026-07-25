# Motor Recovery System — hackathon build

TENS-pad system to help people relearn arm/hand motions post-injury.
Camera → pose estimation → 3D digital twin → user picks a target motion →
TENS pads fire to drive the muscle through it. Secondary feature: photo-based
pad-placement guidance (where to stick each pad).

Built for the Juno x Anthropic Consumer Health Hackathon, Jul 25–26 2026,
Encode Hub, London. Team: Jack (hardware), Faisal (hardware), Siona
(pad-placement research), Atilade (software/pipeline).

## Architecture

Single Python backend (this repo) + a separate web frontend (Three.js digital
twin, owned by another teammate) talking over one WebSocket. The backend also
talks to the TENS board over WiFi/TCP (board is untethered, on a mobile
hotspot with a range-boosted adapter).

```
Pixel 7 (video) --> backend/pose/broadcaster.py (MediaPipe, one shared instance) --\
                                                                                     >-- WebSocket --> frontend (Three.js twin, motion picker)
calibration photos --> backend/placement                                          /
                                                    backend/actuation/controller.py --> backend/driver (TCP) --> TENS board (WiFi)
```

- `backend/pose/estimator.py` — MediaPipe wrapper. `ArmPoseEstimator` (VIDEO mode, stateful) drives the live stream; `detect_pose_in_image` (IMAGE mode, one-off) drives calibration-photo processing. Both lock onto one arm side once it clears a visibility threshold (`config.MIN_LANDMARK_VISIBILITY`) instead of re-picking every frame/photo — verified live that re-picking per frame can track the wrong (out-of-frame) arm, and per-photo re-picking risked the 5 calibration shots referring to different physical arms.
- `backend/pose/broadcaster.py` — owns the *one* camera + pose model shared across all WebSocket clients (rather than opening a new camera/model per connection), pushing pose updates to a per-client queue.
- `backend/placement/geometry.py` — joint-percentage placement for wrist + front/rear delt pads
- `backend/placement/markers.py` — tape-dot detection + Lucas-Kanade optical flow for bicep/tricep, with dimension/size/displacement validation (isometric flex doesn't move joint landmarks, so geometry alone can't localize it — dot displacement between flexed/relaxed photos does)
- `backend/placement/overlay.py` — draws a labeled marker on a pad's own source photo and PNG/base64-encodes it, so `/placement` hands back an image showing exactly where the pad goes, not just coordinates a frontend has to render
- `backend/placement/hsv_calibration.py` — samples a small patch of a real tape photo and suggests `MARKER_HSV_LOWER`/`MARKER_HSV_UPPER`, used by `scripts/calibrate_tape_color.py`
- `backend/placement/pipeline.py` — wires the above into `compute_placement()`, called by `POST /placement`
- `backend/driver/tens_client.py` — TCP client to the board. Validates pad/intensity/duration against the allowlist and bounds, and requires the board's JSON response to actually say `{"status": "ok"}` — any other reply (non-JSON, missing status, status != "ok") is treated as failure, not silently accepted.
- `backend/actuation/controller.py` — the safety seam between the WebSocket and the driver: single-flight + cooldown rate limiting, runs the blocking socket call off the event loop, guarantees a best-effort STOP on cleanup. **Nothing should call `TensClient` directly except this controller.**
- `backend/main.py` — FastAPI app: `/ws` streams pose + handles motion selection through the controller, `POST /placement` runs calibration photos through the pipeline
- `backend/config.py` — every placeholder/assumed value lives here, plus `assert_real_mode_allowed()` (see Safety below)

## Why pads are split into two placement strategies

Bicep/tricep are placed via **tape-dot + optical flow** because isometric
flexion (tensing without moving the arm) doesn't change joint-landmark
positions — you need to see the muscle physically bulge under a marker.
Wrist and front/rear delt use a simpler **joint-percentage heuristic**
(pad = a fixed % of the distance between two landmarks).

Front vs. rear delt specifically: a single 2D photo has no front/back depth,
so `delt_pad_point()` takes no side flag — call it once on a front-on photo's
landmarks and once on a back-on photo's landmarks instead of trying to infer
both from one shot.

## Calibration photo protocol (`POST /placement`)

5 stills, camera and body position fixed within each pair so only the
muscle changes between shots. Tape dots (bright colour, see
`config.MARKER_HSV_*`) along the bicep/tricep line for the first three:

| field           | what it shows                                              |
|-----------------|-------------------------------------------------------------|
| `relaxed`       | side-on, arm bent ~90°, sleeves off, muscles relaxed         |
| `bicep_flexed`  | same framing as `relaxed`, bicep flexed (e.g. curling in)     |
| `tricep_flexed` | same framing as `relaxed`, tricep flexed (e.g. pushing straight) |
| `front`         | front-on, arm slightly raised, shoulder visible               |
| `back`          | back-on, arm slightly raised, shoulder visible                |

Always returns `200` with:
```json
{
  "calibration_complete": false,
  "pads": {
    "wrist":      {"ok": true,  "point": {"x": 0.4, "y": 0.6}, "detail": null, "overlay_b64": "<png, base64>"},
    "bicep":      {"ok": true,  "point": {...}, "detail": null, "displacement_px": 22.1, "overlay_b64": "<png, base64>"},
    "tricep":     {"ok": false, "point": null, "detail": "no marker moved enough...", "overlay_b64": null},
    "front_delt": {"ok": true,  "point": {...}, "detail": null, "overlay_b64": "<png, base64>"},
    "rear_delt":  {"ok": false, "point": null, "detail": "'left' arm not confidently detected in this shot", "overlay_b64": null}
  }
}
```
A per-pad failure never turns into a request-level error — you get a
partial result with `calibration_complete: false` and a specific reason per
pad, not an all-or-nothing outcome. `422` is reserved for actually malformed
input (an upload that isn't a decodable image). The `front`/`back` shots
reuse whichever arm side the `relaxed` shot locked onto (see the pose side-
locking note above) rather than each independently guessing — if `relaxed`
fails, `front_delt`/`rear_delt` fail too rather than risk picking a
different arm with nothing to lock to.

`overlay_b64` is a PNG (base64-encoded) of the pad's own source photo with a
labeled marker drawn at its point — `wrist` on `relaxed`, `bicep`/`tricep` on
their own `*_flexed` shot (the frame the optical-flow point was actually
tracked into), `front_delt`/`rear_delt` on `front`/`back`. This is the
"draws a visual overlay" part of the pad-placement guidance feature — it's
a real annotated image, not just the raw `point` coordinates. **Not yet
confirmed with whoever's building the frontend** — they may prefer to draw
their own overlay from `point` instead of displaying this image directly;
same caveat as the WebSocket schema below.

Tested against a live server with synthetic images (200 with a mixed
ok/failed breakdown, confirmed the wrist/delt failures don't block the
independently-successful bicep/tricep detections); the malformed-input 422
path; and a mocked unit test for the full-success shape
(`tests/test_placement_pipeline.py`) since real success needs actual tape
dots on a real arm to exercise for real.

## Camera

Two different needs, don't conflate them:
- **Calibration photos**: just transfer stills normally (AirDrop/WhatsApp/etc), no streaming needed.
- **Live demo tracking**: assumed to be the Pixel 7 running the "IP Webcam" Android app, streamed over WiFi to `cv2.VideoCapture(<url>)`. Swap `VIDEO_SOURCE` in config/env if using something else. Verified live: needs decent, non-backlit lighting and the camera pulled back enough to see the full torso + arm through its range of motion — a tight, overexposed close-up produces confident-looking but visually wrong landmark output rather than an honest "no detection."

## Safety — real hardware fires real current into a person

`ActuationController` + `TensClient` + `config.assert_real_mode_allowed()`
form a layered guard, reviewed by Tap (COBOL/audit background, asked for a
second opinion on this specifically) before real hardware ever touches
this code:

- **Two independent env vars gate real mode**, separate from
  `DRIVER_MOCK_MODE` itself: `BOARD_CONTRACT_CONFIRMED=true` and
  `RELAY_PAIRING_CONFIRMED=true`. Flipping `DRIVER_MOCK_MODE=false` alone
  does nothing — `TensClient.__init__` calls `assert_real_mode_allowed()`
  and refuses to construct (raises `RuntimeError`) unless both are also
  set *and* `BOARD_HOST` is no longer the placeholder default. One
  accidental env change shouldn't be enough to arm real hardware.
- **Pad/intensity/duration are validated twice**: once in
  `ActuationController.fire()` (the primary check, since nothing else
  should call the driver directly) and again in `TensClient.fire()`
  (defense in depth, in case something ever does call it directly).
  Unknown pad names and out-of-range intensity/duration are rejected
  before anything reaches the socket.
- **Single-flight + cooldown**: `ActuationController` rejects a new fire
  command while one is already in flight, and enforces
  `config.ACTUATION_COOLDOWN_S` between commands — no client can spam
  actuation.
- **STOP on cleanup**: `ActuationController.stop()` is called on WebSocket
  disconnect, error, and app shutdown (see `main.py`'s `finally` block and
  `_lifespan`). It never raises — safe to call from any cleanup path.
- **Board responses are validated**, not trusted: `TensClient._send()`
  requires a JSON reply with `"status": "ok"`; anything else (non-JSON,
  missing status, status != "ok") raises rather than being reported as
  success.
- **The board itself must have its own independent timeout/watchdog** —
  software-side cleanup is not a safety guarantee on its own. Confirm this
  exists on the hardware side before real use.
- Before any of this touches a real arm: **run through a dummy load first**
  (a resistor, an old TENS pad on a non-person surface — anything that
  proves the board fires/stops correctly without a person attached), not
  as a formality but as the actual first real-hardware test.

## Sending pose to the control loop (hardware team)

We are the **pose estimator**. The control loop (`controller/run.py`, separate
repo) is the **receiver**. Data flows one way — we push UDP datagrams at it,
there is nothing here for it to call. Full contract in
[`docs/POSE_OUTPUT.md`](docs/POSE_OUTPUT.md); the spec we implemented against
is their `POSE_API.md`.

```
POSE_UDP_ENABLED=true POSE_UDP_HOST=<controller ip> uv run uvicorn backend.main:app
```

Off by default on purpose: enabling it starts sending pose to whatever is
listening, which on their rig drives a limb.

| | |
|---|---|
| Transport | UDP, JSON, one object per datagram (~126 bytes) |
| Default target | `127.0.0.1:9090` (`POSE_UDP_HOST` / `POSE_UDP_PORT`) |
| Rate | ~28-32 Hz measured (their band is 20-60) |
| Units | metres, `+X` subject forward, `+Y` subject's left, `+Z` up |

```json
{"shoulder":[0.0,0.0,0.0], "elbow":[0.0,0.0,-0.3],
 "wrist":[0.167,0.0,-0.499], "timestamp":1784999572.058932}
```

### Verify the coordinate frame before connecting the board

**Do this first, every time the camera setup changes.** Wrong axes do not raise
an error anywhere — nothing fails, nothing logs, the limb just gets driven the
wrong way. It is the one bug in this integration that will not announce itself.

`scripts/pose_receiver.py` stands in for the controller. Standard library only,
so it runs on any machine without installing anything:

```
python scripts/pose_receiver.py --check     # guided: verifies each angle moves the right way
python scripts/pose_receiver.py             # live readout of angles, rate and staleness
```

`--check` walks through four movements (bend elbow, raise forward, raise
sideways, return to rest) and reports whether each derived angle moved in the
direction it should. It either clears you to connect the board or tells you not
to, with the likely causes ranked.

It mirrors the real receiver's behaviour — same 300 ms staleness rule, same
`alpha = 0.35` low-pass filter, malformed datagrams counted rather than fatal —
so what you read here is what their controller sees.

### Two pose feeds, opposite behaviour when tracking is lost

This trips people up, so it is worth stating plainly. The same lost-arm event is
reported differently to each consumer, deliberately:

| | WebSocket `/ws` (the twin UI) | UDP (the control loop) |
|---|---|---|
| Arm lost | **keeps publishing**, `landmarks: null` + a `status` | **sends nothing at all** |
| Why | the UI must prompt "get your arm in the camera" | silence is what stops stimulation |
| Coordinates | normalized (no scale) | metres |

Do not wire the UDP sender off the WebSocket payload, and do not "helpfully" add
a fallback pose to the UDP path. Both would look like tidying up and both would
break the safety model. The rule lives in `backend/pose/control_link.py`.

Note the coordinate difference too: the WebSocket carries normalized landmarks,
whose `z` sits on an unrelated scale to `x`/`y` (measured ~23x). They are fine
for the twin, which only needs relative shape, and wrong for anything metric.

## Open items — confirm before relying on these

These are coded as placeholders in `config.py` so the software runs and is
testable now, but are **not verified**:

1. **Board IP/port + exact JSON field names** — Jack/Faisal confirmed TCP,
   JSON, pad+intensity+duration conceptually, but not the literal contract
   or how to reach the board's IP on the hotspot. Blocks flipping
   `BOARD_CONTRACT_CONFIRMED=true` for real (see Safety above).
2. **4th relay pairing** — confirmed: bicep/tricep and front-delt/rear-delt
   share relay outputs as antagonist pairs. The wrist pad's pairing is
   **assumed** to be wrist-flexor/wrist-extensor (matches the antagonist
   pattern of the other two pairs) but Jack hasn't confirmed what's actually
   wired to that 4th output. Blocks flipping `RELAY_PAIRING_CONFIRMED=true`
   for real.
3. **Placement % offsets** (`WRIST_PAD_OFFSET_PCT`, `DELT_PAD_OFFSET_PCT`) —
   rough guesses, not backed by Siona/Amara's research. Only one raw
   measurement came back so far (elbow-to-wrist crease ≈ 30cm), which is more
   useful for scaling the 3D twin than for these percentages. Both are now
   env-overridable (see below) so a real number can be dropped in without a
   code change.
4. **WebSocket schema** — the contract documented at the top of `main.py` is
   my proposed default. Confirm with whoever's building the frontend before
   it's load-bearing.
5. **`/placement`'s `overlay_b64` contract** — same "confirm with frontend"
   caveat as the WebSocket schema. Built on the assumption the frontend
   wants a ready-to-display annotated image; if they'd rather draw their own
   overlay from `point`, this is dead weight (bytes over the wire, not a
   correctness problem) but still worth confirming.
6. **Tape-dot HSV range** (`MARKER_HSV_LOWER`/`MARKER_HSV_UPPER`) — still the
   placeholder pink/magenta guess in `config.py`. Untested against the actual
   tape. Run `scripts/calibrate_tape_color.py` against a real photo of the
   tape before relying on bicep/tricep detection (see Running below) — this
   and the two calibration steps below all need an actual camera + arm +
   tape session, which I can't do without you.
7. **Live end-to-end run** — the full 5-photo pipeline and bicep/tricep
   marker tracking have only been exercised against synthetic/mocked images
   in tests, never a real arm with real tape dots. Needs a live session
   before the demo, ideally the same session as the HSV calibration above.

## Explicitly deferred (from Tap's review, not done — hackathon scope call)

Tap (hub agent, second opinion) also flagged these as correct improvements
that this build deliberately does *not* implement, given only one client
(the demo frontend) will ever be connected and there isn't time for
production-grade rigor in a 48-hour build:

- Multi-client session ownership/locking beyond the single-flight lock
  already in `ActuationController` — fine for one connected client, not
  safe if multiple people could ever command the board at once.
- A consent-cleared golden-image/frame corpus for repeatable MediaPipe
  regression testing.
- Marker motion-compensation (subtracting global camera/body movement
  rather than assuming the camera is perfectly still between shots).
- The full 7-category test matrix Tap proposed — implemented the safety-
  critical subset (config guard, fake-board wire protocol, actuation
  controller, pose side-locking, marker robustness, FastAPI endpoint
  behavior); skipped exhaustive edge-case coverage of every failure mode.

## Running

Env is managed with [uv](https://docs.astral.sh/uv/) — `uv sync` installs
everything from `pyproject.toml`/`uv.lock`, no manual venv/pip needed.

```
uv sync
uv run python scripts/download_pose_model.py   # one-off, fetches models/pose_landmarker_lite.task (not committed)
uv run uvicorn backend.main:app --reload
```

Note: `mediapipe`'s old `mp.solutions.pose` API is gone as of 0.10.35 — this
uses the current Tasks API (`mp.tasks.vision.PoseLandmarker`), which needs
that downloaded model file. If mediapipe ships breaking API changes again,
`backend/pose/estimator.py` is the only file that touches it.

Tests (safe to run anytime, no hardware/camera needed):
```
uv run pytest
```

`frontend/twin.html` loads real muscle meshes over `fetch`, which browsers
block on `file://` pages — serve it locally instead of double-clicking it:
```
python -m http.server 8080 --directory frontend
```
then open `http://localhost:8080/twin.html` (with the backend running
separately per above).

### Tuning tape color and placement offsets on-site

`WRIST_PAD_OFFSET_PCT`, `DELT_PAD_OFFSET_PCT`, `MARKER_HSV_LOWER`, and
`MARKER_HSV_UPPER` all read from the environment (falling back to the
placeholder guesses in `config.py`), so these can be tuned at the venue
without touching code:
```
WRIST_PAD_OFFSET_PCT=0.12 DELT_PAD_OFFSET_PCT=0.15 \
MARKER_HSV_LOWER=140,80,80 MARKER_HSV_UPPER=170,255,255 \
uv run uvicorn backend.main:app --reload
```

To get real `MARKER_HSV_*` values instead of guessing: take a photo of the
actual tape, open it in any viewer to read off the pixel coordinates of one
dot, then run
```
uv run python scripts/calibrate_tape_color.py <photo path> <x> <y>
```
It prints the two env vars to export and writes `<photo path>_mask.png` —
open that and confirm it's white only where the tape dots are (nothing
else) before trusting the suggested range. If it's too noisy, the script's
`tolerance` parameter (see `backend/placement/hsv_calibration.py`) can be
tightened.
