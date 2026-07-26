# Code Review — juno_hack

**Scope:** whole working tree — `firmware/`, `controller/`, `tools/`, `docs/`, `axon-main/`.
**Lens:** internal consistency, correctness, safety, and whether what is written down
matches what the code actually does.
**Date:** 2026-07-25

---

## Summary

This is unusually well-documented for a hackathon build, and the safety architecture is
real rather than aspirational — the ESP32 asks `stim_allowed()` on every service pass, not
only when a packet arrives, so relays open on silence with no cooperation from the PC.
Most of what follows is drift between three layers of documentation and one codebase, plus
a small number of genuine defects.

Four issues should be closed before this drives a person: a kinematics saturation that
pins the controller at maximum duty against an unreachable target, a documented staleness
mechanism that is not implemented, an antagonist-safety invariant that is claimed to be
enforced in firmware but is not, and an unauthenticated command link. Everything else is
drift, dead code, or polish.

**Verdict: Request Changes** — items 1–4 before hardware-on-person; the rest at leisure.

---

## Critical

| # | File | Line | Issue | Severity |
|---|------|------|-------|----------|
| 1 | `controller/kinematics.py` | 74 | `shoulder_flex` measurement saturates at exactly 90° | 🔴 Critical |
| 2 | `controller/pose_api.py` | 118–137 | Documented `timestamp` staleness check is not implemented | 🔴 Critical |
| 3 | `firmware/lib/stim_array.py` | 46–69 | Antagonist co-contraction not blocked in the safety layer | 🔴 Critical |
| 4 | `firmware/lib/net_udp.py` | 66–155 | Command link is unauthenticated | 🟠 High |

### 1. `shoulder_flex` saturates at 90°, and the controller cannot tell

```python
# kinematics.py:71-75
forward, lateral, vertical = v[0], v[1], v[2]
down = -vertical
flexion = math.degrees(math.atan2(forward, max(down, 1e-9)))
```

Once the upper arm passes horizontal, `down` goes negative and `max(down, 1e-9)` clamps it,
so `atan2` can never return more than 90°. Measured, with the repo's own FK convention:

| true flexion | 45° | 89° | 90° | 100° | 110° | 130° |
|---|---|---|---|---|---|---|
| **measured** | 45.00 | 89.00 | 90.00 | **90.00** | **90.00** | **90.00** |

`JOINT_LIMITS["shoulder_flex"]` is `(-20.0, 110.0)` and `docs/POSE_API.md` documents the
same range, so a target above 90° is reachable by the arrow keys and unreachable by the
measurement. The consequence is not a bad reading — it is a stuck loop: error freezes at a
constant, the integrator winds to `i_limit`, and CH3 sits at `DUTY_MAX` indefinitely, broken
only by the firmware's 4 s burst / 2 s cooldown. The status line will show `act 90.0
tgt 110.0` forever while the anterior deltoid is driven flat out.

Either clamp `JOINT_LIMITS` and `POSE_API.md` to 90°, or use the signed vertical component
(`atan2(forward, down)` without the clamp handles the full range correctly).

**Related:** flexion and abduction are each measured independently against `down`, so they
cross-couple. Round-tripping the repo's own `forward_kinematics` back through
`shoulder_angles`:

| input | flex 0° / abd 30° | flex 30° / abd 30° | flex 60° / abd 30° | flex 45° / abd 45° |
|---|---|---|---|---|
| **abduction out** | 30.00 | 33.69 | **49.11** | **54.74** |

19° of phantom abduction at 60° flexion. `forward_kinematics` and `shoulder_angles` are
documented as the same convention but are not inverses.

### 2. The pose staleness contract is not implemented

`docs/POSE_API.md` tells the estimator team:

> 2. **Do not send a guess.** If tracking is lost or the arm is occluded, **stop sending**
>    (or send with an old timestamp).
> 3. **Timestamp every message.** It is how we detect staleness.

`PoseReceiver` never reads `timestamp`. Staleness is arrival-time only — `_ingest()` sets
`_last_rx = time.monotonic()`, and `latest()` compares against that. So the parenthetical
option in requirement 2 is a no-op: an estimator that keeps streaming a frozen pose with an
old timestamp will be treated as fresh, and the controller will keep driving the limb
against a pose that stopped being true — the exact failure the document opens by warning
about. Requirement 3's stated reason is false.

This has already propagated: `axon-main/backend/pose/control_link.py:76` carries the comment
*"Required: it is how they detect staleness (>300ms stops stimulation)"*.

Fix one way or the other — implement the check in `_ingest`, or delete the timestamp option
from requirement 2 and correct requirement 3. Do not leave the doc promising a safety
mechanism that does not exist.

### 3. Antagonist safety is enforced in the layer that is explicitly not trusted

`README.md`: *"Antagonist pairs are **never** co-contracted."*
`docs/SAFETY.md`: *"The controller is the performance layer. The firmware is the safety
layer. Nothing in `controller/` is trusted by `firmware/`."*

But the rule lives only in `controller/mapping.py`. `StimArray.apply()` applies the duty
vector verbatim:

```python
# stim_array.py:61-68
for i, name in enumerate(P.CHANNEL_ORDER):
    self.channels[name].set_duty(self.safety.clamp_duty(seq[i]))
if grip:
    self.channels["CH7"].set_duty(...)   # the ONLY pair the firmware protects
    self.channels["CH8"].set_duty(0.0)
```

A packet with CH1 = CH2 = 0.7 locks the elbow solid, and every layer the safety docs point
at will permit it. `test_simulation.py` §3 tests the invariant — through `mapping`, i.e. the
untrusted side. Add a pairwise check to `apply()` (zero the weaker of any conflicting pair,
or reject the packet) so the claim matches where it is enforced.

### 4. Anyone on the network can arm the board

`CommandLink` accepts any well-formed JSON datagram on :8080 — no shared secret, no source
filter — and `self._peer` becomes whoever sent last. `{"arm":true,"duty":[0.7]*8}` from any
host on the hotspot starts stimulation. `PoseReceiver` likewise binds `0.0.0.0:9090` and
accepts pose from anyone, which is the input the control loop drives on.

For an isolated hotspot in a demo this may be an accepted risk, but `docs/SAFETY.md`'s layer
table lists *"buggy or malicious commands"* as covered by the firmware clamps — true for
magnitude, not for authority. Either add a shared token to the packet schema, or state the
assumption in SAFETY.md so nobody later runs this on venue Wi-Fi believing it is covered.

---

## High

| # | File | Line | Issue |
|---|------|------|-------|
| 5 | `firmware/boot.py` | 40 | Secrets fallback import can never succeed |
| 6 | `tools/deploy_wifi.py`, `firmware/boot.py`, `MY_SETUP.md` | 131, 57, — | WebREPL password hardcoded in tracked files |
| 7 | `controller/test_simulation.py` | 204, 213 | Two assertions that cannot fail |
| 8 | `firmware/lib/stim_array.py` | 121–128 | `press_timer_now()` blocks the loop for 250 ms |
| 9 | `controller/control_loop.py` | 145 | Grip release (CH8) is unreachable |
| 10 | `firmware/main.py` | 201 | `import boot` — the thing `netstate.py` exists to prevent |

**5.** `from config import device_secrets_example as SEC` — the file is
`device_secrets.example.py`, whose dot makes it non-importable under that name. Verified:
`ImportError: cannot import name 'device_secrets_example'`. The outer `except` swallows it,
so a board with no secrets prints `[boot] network setup skipped:` instead of the intended
warning, and comes up with no Wi-Fi at all. Rename the file to `device_secrets_example.py`
or drop the fallback.

**6.** `juno2026` appears as a default in `deploy_wifi.py:131`, as a fallback in
`boot.py:57`, and in plain text in `MY_SETUP.md`. Gitignoring `device_secrets.py` protects
nothing while the same secret sits in three tracked files. Same for
`AP_PASSWORD = "juno12345"` in `firmware/config/settings.py:65`. Read the password from
`device_secrets` in both tools and remove it from the docs.

**7.**
```python
check("channels actually energise under service()", any_on or True, ...)   # always True
check("apply() refused after watchdog expiry", array.apply([0.5]*8,) is not None)
```
The second is true for both `True` and `False` returns, and the `sup.note_command()` two
lines above means the watchdog is *not* expired — the check tests the opposite of its name.

**8.** `press_timer_now()` calls `sleep_ms(250)` from the main loop. `settings.py` documents
`LOOP_YIELD_MS = 1` as necessary for PWM granularity and scheduler health; during that
250 ms nothing advances — no PWM phase, no e-stop poll, no watchdog service. `_service_timer()`
already implements the non-blocking assert/release pattern; reuse it and make
`press_timer_now()` just prime the same state machine.

**9.** `mapping.efforts_to_duties()` accepts `release=`, and CH8 is documented as "grip
release / finger extensors" everywhere. `control_loop.step()` never passes it, so toggling
`G` open merely stops CH7 — the extensors are never driven. Also note grip travels twice:
inside the duty vector *and* as the `grip` flag, with `StimArray.apply()` re-asserting CH7
at `DUTY_MAX`. Pick one path.

**10.** `netstate.py`'s entire docstring explains that `import boot` re-executes the file
(Wi-Fi reconnect, WebREPL restart, duplicated banner) because MicroPython does not register
`boot` in `sys.modules`. `main.py:201` then does exactly that in its `KeyboardInterrupt`
handler. Inline a local `all_off()` instead.

---

## Medium

**11. Documentation drift — verified against the code**

| Claim | Where | Reality |
|---|---|---|
| deadband "~5–6°" | `README.md:251`, `run.py:160` | `settings.py:50-52` → **3.0** |
| `GAINS` snippet showing 5.0/6.0/6.0 | `docs/CONTROL.md:221-223` | same doc says 3° at line 117 — self-contradictory |
| "37 offline checks" | `README.md:148`, `docs/TESTING.md:55` | actual run: **48 checks, 0 failed** |
| "Expect 46 checks" | `MY_SETUP.md:245` | actual: **48** |

The deadband number is load-bearing: `run.py`'s on-screen help tells the operator to press an
arrow 2–3 times because the band is "~5-6 deg", when one 3° press now clears it.

**12. Tick arithmetic wraps.** `stim_channel.py:96` (`self._cooling_until = now + cooldown_ms`)
and `stim_array.py:118` (`self._timer_press_until = now + P.TIMER_PRESS_MS`) use raw addition.
MicroPython's `ticks_ms()` wraps, which is why `ticks_diff` exists; the matching constructor is
`time.ticks_add`. Not a demo-horizon risk, but it is a real defect in a cooldown timer.

**13. The e-stop ISR comment does not match the ISR.** `main.py:44` says *"Keep ISR work
minimal: flag + de-energise, no allocation"* — `safety.kill()` builds and returns a dict and
`array.all_off()` iterates a dict. This is safe today only because ESP32 `Pin.irq()` defaults
to a scheduled (soft) handler; it breaks the moment someone adds `hard=True`. Either make the
handler genuinely allocation-free or correct the comment.

**14. `disarm` loses to `arm`.** `main.py:145-151` — `kill` correctly outranks `arm` in a
merged batch (`net_udp.poll()` forces `arm=False`), but `arm` still wins over `disarm`. The
fail-safe ordering should be kill > disarm > arm.

**15. `PIController.out_min` is dead.** Stored at `pid.py:38`, never read; both the deadband
hold and the final clamp use `±out_max`. Harmless with today's symmetric config, silently
wrong for anyone who sets an asymmetric range.

**16. `calibrate.py` can advise raising the current in response to a comms failure.** It
sends `arm=True` once (line 111) and never confirms it against the heartbeat — `bench.py`
does exactly this confirmation and explains why. If that one UDP packet is lost, the whole
sweep records a flat curve and the tool concludes:

> `WARNING: no movement detected. Check electrode placement, raise the hand-set intensity level`

Confirm arm from the status reply, or abort.

**17. `pose_api._parse` invents missing angles.**
`{j: float(msg.get(j, 0.0)) for j in self.JOINTS}` — an estimator sending only `elbow` yields
`shoulder_flex = 0.0`, `shoulder_abd = 0.0` reported as measured truth, and the controller
drives to close a fabricated error. Reject incomplete messages into `_bad` instead. Minor
related nit: `isinstance(True, int)` is `True`, so `{"elbow": true}` parses as 1.0°.

**18. `control_loop.step` comment describes a mechanism it does not use.** *"We stop
commanding and let the firmware watchdog take it from there"* — it keeps sending zero-duty
packets (line 137), which pets the watchdog. The behaviour is fine and arguably better; the
comment should say so.

**19. The repo tells three different stories about what it is.**

- `AGENTS.md` — a *Neuromuscular Aim Assistant* playing Assault Cube through a human arm, on
  an **Axiometa Genesis Mini (ESP32-S3-MINI-1-N4R2, 4 MB/2 MB)**, watchdog 500 ms, *"max
  ~300 ms stim per burst"*.
- `README.md` — a closed-loop FES arm teleoperator, on a **Goouuu ESP32-S3-N16R8 (16 MB/8 MB
  octal)**, `MAX_BURST_MS = 4000`.
- `axon-main/README.md` — a post-injury motor-recovery system for the Juno × Anthropic
  Consumer Health Hackathon.

`AGENTS.md` also points at `../ROADMAP.md` and `../docs/AUVON-AS8016-datasheet.pdf`; neither
path resolves (the datasheet is at the repo root). Since `AGENTS.md` is the first thing an
agent reads, its board spec and burst limit actively contradict the firmware it will edit.

**20. Two incompatible board protocols coexist.** `axon-main/backend/driver/tens_client.py`
speaks TCP JSON `{"pad","intensity","duration_ms"}` on :5005 to "the TENS board". The TENS
board is `firmware/`, in this same tree, and it speaks UDP `{"duty":[8],"grip","arm","seq"}`
on :8080. axon-main's open item #1 — *"Board IP/port + exact JSON field names … blocks
`BOARD_CONTRACT_CONFIRMED`"* — is blocked on a contract that already exists two directories
away and does not match it. The pad model (6 named pads across 3 relay pairs) also
contradicts the firmware's 8 channels. Worth an explicit note in both READMEs saying which
actuation path is live.

**21. `TensClient.stop()` violates its own bounds.** It sends `duration_ms: 0` while
`MIN_DURATION_MS = 100`. Bypassing validation for STOP is the right call, but if the board
enforces the same range, the one command that must never be rejected is the one that breaks
the rules. Pin this when the board contract is confirmed.

**22. Python version split.** Root `.venv` is **3.13.0** and `requirements.txt` says "Python
3.8 or newer"; `axon-main/pyproject.toml` requires **`>=3.14`** with `.python-version` 3.14
and uv. The documented root venv cannot run `axon-main`, and neither README mentions the
other project's toolchain.

**23. `deploy_wifi.py`'s file list is hand-maintained.** A new module under `firmware/lib/`
is silently not deployed — and `FIRMWARE_VERSION` exists precisely to catch "did my deploy
take?", which this failure mode defeats (the version file *does* get copied). Walk the tree
instead of listing 14 paths.

---

## Low / nits

- `tools/calibrate.py` docstring says output is `calibration_<channel>.json`; it writes
  `calibration_ch<N>.json`.
- `pins.assert_no_conflicts()` never checks `ESTOP_PIN` — GPIO8 is validated against neither
  `RESERVED` nor the channel pins.
- `stim_array.py:41` `self._last_seq = -1` is dead; sequence handling lives in `CommandLink`.
- `settings.COMMAND_RATE_HZ` is never read by the firmware.
- `bench.py:233` comment "full duty = solid ON" — it is clamped to 0.70 and buzzes; the
  `print`s immediately below get it right.
- `EspLink.poll_status` catches `(OSError, BlockingIOError)`; the latter is a subclass of the
  former.
- `broadcaster.TARGET_FPS` no longer sets a target (pacing was deliberately removed); only
  `FRAME_INTERVAL_S / 4` still uses it.
- `link.py:123` binds `locals_`, shadowing the builtin name.
- `test_simulation.py` is a bespoke script while `axon-main` uses pytest; the root suite
  can't be collected by `pytest` and can't be filtered or run per-case.
- There is no `.git` directory, despite two `.gitignore` files whose comments discuss commit
  history and history rewrites. Worth `git init`-ing before the calibration photos and
  `device_secrets.py` protections actually matter.

---

## What looks good

Called out because it is genuinely above the bar for a build at this stage:

- **The role split is real, not just documented.** `array.service()` asks
  `safety.stim_allowed(now)` every pass, so the watchdog opens relays without needing a
  packet, a thread, or any cooperation from the PC.
- **Fail-safe by construction throughout:** active-low relay modules whose input pull-ups
  hold every channel open while the ESP32 pins float at boot; the NC dummy-load resistor that
  prevents the turn-on jolt; a normally-closed e-stop where a cut lead trips the kill;
  all-off before Wi-Fi in `boot.py`.
- **The e-stop is covered three ways** — IRQ, boot-time level check, debounced poll — with a
  written explanation of why the edge-triggered IRQ alone cannot see "already pressed at
  power-on" or "lead fell off mid-run". The debounce rationale (a latched kill from one noisy
  sample looks like a random permanent failure) is exactly the right thing to have thought
  about.
- **`net_udp.poll()`'s split merge rule** — newest-wins for duty, OR-across-the-batch for
  control flags, kill outranking a later arm — is subtle, correct, and pinned by §9 of the
  test suite over real sockets, including the seq-resync regression that the troubleshooting
  table in `MY_SETUP.md` documents.
- **Deadband hold instead of collapse-to-zero**, with the limit-cycle reasoning, measured
  before/after numbers, and tests that pin both the improvement *and* that the hold is
  cleared by disarm and by kill.
- **`axon-main`'s real-mode gate**: two independent confirmation env vars *plus* a
  placeholder-host check, enforced in the constructor. The env parsing is deliberately
  asymmetric — `!= "false"` for mock mode, `== "true"` for the confirmations — so every
  malformed value lands on the safe side.
- **`to_control_frame`** is a correct right-handed rotation (verified: determinant +1), and
  the signs are unit-tested against physical poses rather than trusted — with a comment
  saying why (wrong axes fail silently and drive the limb the wrong way).
- **The opposite-contract pose feeds** (UI is told the arm is lost; the control loop hears
  silence) with an explicit "do not tidy this up, both changes would look like cleanup and
  both would break the safety model" warning.
- Comments consistently explain **why**, and several record the specific bug that motivated
  them — `test_simulation.py` §8's header is a good example.
