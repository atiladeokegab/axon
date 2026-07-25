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

## Their note on our filter constant — accepted, not yet changed

They flagged that `POSE_FILTER_ALPHA = 0.35` was tuned without a known sample
rate. At their measured 28 Hz it gives a time constant of ~83 ms (~250 ms to
settle), which sits on top of our existing 150–300 ms loop delay.

We are leaving it for now because our closed-loop bandwidth is only ~0.5–1 Hz
and settling already takes seconds, so ~83 ms is not the limiting term. It is
worth revisiting **after** we measure real pose noise on the rig: the deadband
(3°) must exceed that noise, and filtering and deadband trade against each other.
See `CONTROL.md`.

Since they send **raw** positions, our filtering is doing real work and should
not simply be switched off.

---

## Runbook — one command

`tools/launch.py` starts the pose service, waits for it, then hands the terminal
to the controller, and shuts the pose service down when you quit.

```cmd
cd C:\Users\faisa\Desktop\juno_hack

python tools\launch.py --no-board                        :: 1. dry run, nothing stimulated
python tools\launch.py --host 192.168.137.154            :: 2. full system
```

Useful variants:

```cmd
python tools\launch.py --sim                  :: virtual arm, no pose service, no board
python tools\launch.py --sim-hw --host <ip>   :: virtual arm, REAL relays
python tools\launch.py --no-pose --host <ip>  :: pose service already running elsewhere
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
python run.py --host 192.168.137.154
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
