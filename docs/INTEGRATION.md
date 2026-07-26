# Integration with axon-main (pose estimation)

Reply to `axon-main/docs/POSE_OUTPUT.md`.

**Status: no adapter needed.** Their feed satisfies `POSE_API.md` as written. We
fed their documented payload into our receiver unmodified and got the expected
angles back.

---

## Verified on our side

| Check | Result |
|---|---|
| Their example payload parses | ✅ `elbow 40.0°, flex 0.0°, abd 0.0°` |
| Their stated geometry test (0.30 m + 0.26 m at 40°) | ✅ recovered **40.0°** |
| Silence on lost tracking ages out | ✅ `fresh=False` after `POSE_STALE_MS` (300 ms) |
| Malformed / frozen counters | ✅ 0 malformed, 0 frozen |

Their rate (~28 Hz) and datagram size (~126 B) are both inside our limits
(20–60 Hz, 2048 B).

---

## Answers to their open questions

### 1. Subject orientation — front-on is correct

Our rig is a **seated subject, front-on to the camera**, which is what they
built and tested for. No change needed.

Worth stating explicitly because our joint-angle zero depends on it: all three
angles are measured from the **arm hanging at the side** rest pose. That is also
the pose gravity returns the limb to when we drop the duty, which is why the
abduction axis is one-directional (CH5 lifts, gravity lowers).

### 2. Host and port — depends where the two processes run

Our receiver binds `0.0.0.0:9090`, so it accepts from any interface.

- **Same laptop** (expected): their default `127.0.0.1:9090` is correct, nothing
  to change.
- **Separate machines:** point `POSE_UDP_HOST` at the machine running
  `controller/run.py`, and allow inbound UDP 9090 through its firewall — the
  same rule that already covers 8080 (see `MY_SETUP.md`).

### 3. Actuation — confirmed independent, and structurally so

**They are two separate rigs, and the protocols cannot collide.** Concretely:

| | axon-main driver | our board |
|---|---|---|
| Transport | **TCP** | **UDP** |
| Port | **5005** | **8080** |
| Payload | `{"pad":"BICEP","intensity":60,"duration_ms":800}` | `{"duty":[8 floats],"tok":...}` |
| Default state | **mock** (`DRIVER_MOCK_MODE` defaults true) | n/a |

So a stray command from their stack cannot actuate our relays: wrong transport,
wrong port, unparseable schema, and since `2026-07-25.7` our firmware also
**requires a shared `CONTROL_TOKEN`** and silently drops anything without it.

Their side is well-guarded too — real mode needs `BOARD_HOST` off its
placeholder *plus* `BOARD_CONTRACT_CONFIRMED` *plus* `RELAY_PAIRING_CONFIRMED`.

**The rule to keep:** never point their `BOARD_HOST` at our ESP32. Their
concern is exactly right — our safety rails (watchdog, duty ceiling,
burst/cooldown, antagonist refusal) arbitrate only commands arriving on our
link, and theirs only arbitrate their own. Two controllers on one relay board
would defeat both. Keeping their driver in mock mode is the simplest guarantee.

---

## Their note on our filter constant — acted on

They flagged that `POSE_FILTER_ALPHA = 0.35` was tuned without a known sample
rate. Correct, and it has since been measured and replaced.

The receiver now derives the sample rate from arrival times rather than assuming
one, and the fixed exponential filter is gone. The chain is a rate gate, then a
median window, then a **one-euro** adaptive low-pass whose cutoff tracks the
signal's own speed — so it smooths hard while the arm is held and opens up while
it moves, instead of compromising between the two. On a real capture that gave
the same lag as the old filter for a third less noise.

The deadband is no longer a constant either. Repeated captures on the same rig
gave elbow noise between 2.5° and 10.9°, so the controller measures the residual
noise live and sizes its deadband from that. Full detail in `CONTROL.md`.

**Please keep sending raw positions.** If their side pre-smooths, our noise
measurement reports their filter's output rather than the estimator's real
quality, and the deadband gets sized from a number that no longer means
anything.

### One config lever we may ask them for

`MIN_LANDMARK_VISIBILITY` is now readable from the environment (their default of
0.5 is unchanged). A capture showed ~13 episodes of roughly 200 ms where
landmarks had wandered but still scored above 0.5, appearing downstream as 25–45°
abduction excursions on a motionless subject. `tools/launch.py --min-visibility
0.7` raises it for a session. Dropping those frames is safer than filtering
them: a dropped frame ages out and stops stimulation, a confidently wrong one is
acted upon.

---

## Dependencies

Two separate environments. **Ours needs nothing new** — the controller is
standard-library only, and `requirements.txt` (esptool, mpremote) is already
installed for the board work.

**axon-main needs:**

| What | Notes |
|---|---|
| **`uv`** | Astral's package manager. `winget install astral-sh.uv` |
| Python **3.14** | `uv` fetches it automatically |
| mediapipe, opencv, fastapi, uvicorn, numpy, websockets | `uv sync` from their `uv.lock` |
| **the pose model file** | `models/pose_landmarker_lite.task` — **gitignored, not in the repo** |
| A webcam | 640×480@30fps is what they measured (~28 Hz output) |

That model file is the one that catches people: it is downloaded, not
committed, so a fresh clone fails at import with an unhelpful error.

**`tools/launch.py` does all of this for you on first run** — it runs `uv sync`,
downloads the model, then starts everything. Expect the first run to take
several minutes (mediapipe is a large download); afterwards it is instant.

To do it by hand instead:

```cmd
cd C:\Users\faisa\Desktop\juno_hack\axon-main
uv sync
uv run python scripts/download_pose_model.py
```

Then `python tools\launch.py --skip-setup ...` to skip the checks.

---

## Runbook — one command

`tools/launch.py` starts the pose service, waits for it, then hands the terminal
to the controller, and shuts the pose service down when you quit.

```cmd
cd C:\Users\faisa\Desktop\juno_hack

python tools\launch.py --no-board                        :: 1. dry run, nothing stimulated
python tools\launch.py --host 192.168.137.131            :: 2. full system
```

This also serves the **3D digital twin**. Once running, open:

| | |
|---|---|
| **3D twin** | <http://127.0.0.1:8081/twin.html> |
| **Camera preview** | <http://127.0.0.1:8000/camera.mjpeg> |

**The twin opens in your browser automatically.** Pass `--no-open` to suppress
that (the URL is still printed).

> **The pose service itself is headless — no window ever appears.** It captures,
> runs MediaPipe and emits UDP silently. The twin is a separate static page that
> connects back to it over `ws://127.0.0.1:8000/ws`, and it must be **served over
> HTTP** — opening `twin.html` as a file fails, because browsers block the
> `fetch()` calls it uses to load the muscle meshes.

**Stopping it: press `Q` in the launcher's terminal.** The browser is only a
viewer — closing tabs leaves the pose service and file server running. If the
launcher window was closed abruptly:

```cmd
python tools\stop.py -n     :: list leftovers (8000 / 8081 / 9090)
python tools\stop.py        :: stop them
```

The launcher kills the whole process **tree** on exit. That matters because
`uv run uvicorn` starts uvicorn as a *child* of uv — terminating only uv would
orphan uvicorn, which keeps the webcam and port 8000 locked and makes the next
launch fail.

Useful variants:

```cmd
python tools\launch.py --sim                  :: virtual arm, no pose service, no board
python tools\launch.py --sim-hw --host <ip>   :: virtual arm, REAL relays
python tools\launch.py --no-pose --host <ip>  :: pose service already running elsewhere
python tools\launch.py --no-open --no-board   :: do not open the browser for me
python tools\launch.py --no-frontend ...      :: skip the 3D twin server entirely
python tools\launch.py --pose-only            :: vision + twin, no controller
                                              ::   (frees UDP 9090 for pose_noise.py)
python tools\launch.py --host <ip> --verbose  :: show the pose service's own log
```

### Why two processes rather than one environment

axon-main needs **Python 3.14 + uv + mediapipe/opencv/fastapi**; our controller
is deliberately **standard library only**. Merging them would pull a large
vision stack into the safety-critical control path and tie both to one Python
version. They stay in separate environments — the launcher only co-ordinates
them. It also forces `DRIVER_MOCK_MODE=true` on their side, so their TENS driver
cannot actuate anything (see §3 above).

**First run is slow** — uv has to resolve and download mediapipe. Use
`--verbose` if you want to watch it. The launcher waits up to 90 s.

### Doing it by hand instead

**Terminal 1 — pose service (axon-main):**

```bash
POSE_UDP_ENABLED=true uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — our controller, stimulation disabled:**

```cmd
cd C:\Users\faisa\Desktop\juno_hack\controller
python run.py --no-board
```

Confirm before going further:

- status line shows **`pose:OK`**, not `pose:STALE`
- `elbow` / `flex` / `abd` **`act`** values move sensibly as the subject moves
- arm hanging at the side reads roughly **elbow ~5–15°, flex ~0°, abd ~0°**
- raising the arm forward increases `flex`; out to the side increases `abd`
- covering the camera makes it go `pose:STALE` within ~300 ms

That last one is the important one: it proves requirement 2 end to end — they
stop sending, and we stop driving.

**Then, with the board:**

```cmd
python run.py --host 192.168.137.131
```

Press `A` to arm. Everything in `SAFETY.md` applies from this point.

---

## One caution about closing the loop for real

Until now the "arm" has been simulated, and the simulated arm always moved the
way the controller expected. With a real limb, three things change at once:

1. **The plant gain is unknown per subject** — that is what `tools/calibrate.py`
   is for. Run it before trusting the loop.
2. **Vision noise enters the loop.** The deadband must exceed it or the
   controller will chase jitter.
3. **A wrong sign is no longer harmless.** If an electrode pair is swapped, the
   controller drives *away* from the target and the integrator winds up against
   it. The `--no-board` pass above is what catches this.

Verify direction with `--no-board` first, then a single joint with the board,
then the rest.
