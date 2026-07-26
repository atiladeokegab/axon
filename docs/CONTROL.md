# Control design

We treat the arm as a robotic manipulator: measure joint angles, command target
joint angles, drive muscles until they match. The unusual part is the actuator —
a consumer TENS unit gated by mechanical relays.

---

## The loop

```
target (keyboard)  --+
                     |         error            effort           duty[8]
                     +--> ( - ) -----> PI ---> mapping ---> PWM ---> relays
                          ^                                            |
                          |                                            v
                    measured angle                             muscle contracts
                          |                                            |
                          +-------- pose estimator <---- the arm moves-+
```

Rate: **30 Hz** control, **~6.7 Hz** PWM carrier.

---

## Why PI, and specifically not PID

**The integrator is mandatory.** Holding the elbow at 90° requires a *constant*
muscle torque just to balance gravity. A proportional-only controller can only
produce steady output by sitting at a **permanent non-zero error** — the arm
droops and stays drooped. Worse, muscle gain is unknown per subject and *falls
as the muscle fatigues*, so that droop would drift during a session. The
integrator supplies the steady holding duty at zero error and automatically
ramps up as fatigue sets in.

**The derivative term is actively harmful here.**

| Reason | Detail |
|---|---|
| Noise | Angles come from vision and jitter; differentiating noise produces chatter, and chatter on a mechanical relay is both audible and destructive. |
| Dead time | ~150–300 ms of loop delay makes any derivative estimate stale — it cannot provide the phase lead it would on a fast plant. |
| Already damped | Muscle viscoelasticity + limb damping make the plant heavily over-damped. D exists to tame oscillation; there is none to tame. |

If you see a limit cycle at the setpoint, widen the deadband or lower `Kp` —
**do not add D**.

## Anti-windup

The output saturates constantly (duty clamps at 0 and `DUTY_MAX`), so windup is
a real risk, not a theoretical one. Three mechanisms in `pid.py`:

1. **Conditional integration** — stop accumulating when saturated *and* the
   error would push further into saturation.
2. **Integral bound** — hard cap regardless.
3. **Deadband freeze** — inside the deadband the integrator is frozen so it
   cannot creep while "close enough". (It still *outputs* its learned holding
   duty — see below.)

## The deadband: hold, don't collapse

Inside the deadband the controller **freezes the integrator but keeps supplying
the holding duty it has already learned**. It does *not* return zero.

Holding a limb against gravity requires a *constant* muscle torque. Returning
zero removes that torque, so:

```
at target -> output 0 -> gravity sags the arm -> error exceeds deadband
          -> controller fires -> arm rises -> inside deadband -> output 0 -> ...
```

Measured on the simulated plant, that limit cycle produced **~26 relay
transitions per second at a supposedly steady setpoint** — audible, visibly
twitchy, and destructive to mechanical relay contacts.

| Behaviour inside deadband | Settles at | Ripple | Relay switches / 25 s |
|---|---|---|---|
| return 0 (naive) | 40.48° | 6.63° | 660 |
| **hold integral (current)** | 40.25° | **0.00°** | **0** |

### Consequence: stimulation continues while holding a pose

Physically unavoidable — a muscle must stay contracted to hold a limb up — but
it deliberately changes the safety picture:

- The firmware's `MAX_BURST_MS` (4 s) / `COOLDOWN_MS` (2 s) limiter now applies
  to sustained holds. Verified: stimulate 4 s → forced rest 2 s → resume.
  **The arm sags during the rest, and that is intended** — fatiguing a muscle
  to hold a pose indefinitely is exactly what the limit exists to prevent.
- The held output is bounded by `out_max`, then clamped again by the firmware's
  `DUTY_MAX`.
- Disarm and e-stop clear the hold immediately (both regression-tested).

### Steady-state error tracks the deadband

Error at settle ≈ the deadband, roughly 1:1, because correction stops inside it
and the approach is one-directional (from below):

| deadband | 0° | 2° | 5° | 8° | 12° |
|---|---|---|---|---|---|
| steady error | 0.00° | 0.86° | 4.52° | 7.26° | 11.26° |

So **deadband is the accuracy knob; hold-vs-zero is the smoothness knob** —
they are independent.

## How much of the arm may be live at once

Two rules, and only one of them is about anatomy.

**1. Two channels sharing a current source are never closed together.** Each
AS8016 shares one constant-current source across A1/A2 and another across
B1/B2. Paralleling two electrode pairs across one source splits the current by
impedance, so neither muscle receives its set value — and if one pad lifts, the
other takes the whole current.

The leads are patched so that **each antagonist pair sits on one bank**, which
means every pair sharing a source is already a pair `ANTAGONIST_PAIRS` refuses.
`assert_banks_safe()` checks that invariant at boot rather than trusting it,
because it depends on how the jacks are patched and that is exactly the kind of
thing that changes on a bench without the code being touched. Re-patch biceps
and anterior deltoid onto the same bank and the board will refuse to start.

**2. At most two arm channels may be live at once** (`MAX_CONCURRENT_ARM_CHANNELS`).
Bank-to-bank the outputs are galvanically isolated, so running elbow and
shoulder together is electrically unremarkable — but three simultaneous paths
through one limb means a fast whole-arm movement a blindfolded subject cannot
anticipate, and triples the charge delivered per unit time. Grip is exempt: it
is on its own bank of the second unit, and a grasp that let go whenever the arm
moved would defeat the point of treating the hand as an end-effector.

### What was tried first, and why it was wrong

An earlier version forbade the **shoulder and elbow** from firing in the same
instant. It was the wrong rule twice over: it blocked two *isolated* channels,
which bought no electrical safety, and it cost real speed.

| | time to reach both targets |
|---|---|
| joint-based exclusion | 1.4 s |
| **same-bank refusal + cap of 2** | **0.5 s** |

The lesson worth keeping: derive the constraint from the electrical topology,
not from the anatomy. The hazard lives in the wiring.

### Time-slicing, for when the cap does bite

Exceeding the cap does not refuse the extra channels, it rotates them: each PWM
period belongs to one subset. At 150 ms that is ~3.3 Hz against a limb bandwidth
under 1 Hz, so the arm integrates the average.

A rotating channel owns only 1/N of the wall clock and would deliver 1/N of the
force, and **the integrator cannot recover that** — it is already saturated
holding the limb against gravity. So the in-slot duty is scaled by N. The
ceiling exists to bound charge over time, and that is the time-average:

```
average = min(1.0, wanted × N) / N  ≤  wanted  ≤  DUTY_MAX
```

which cannot exceed the average the channel would have received with no rotation
at all. With two joints moving, N is 1 and the scaling vanishes entirely.

### Grip is the one channel allowed duty 1.0

Every other channel is servoed, so duty is its force knob and `DUTY_MAX` keeps a
margin. Grip is triggered — the hand is an end-effector with two useful states,
and a half-closed grasp is a hand that drops the object. `CH7`/`CH8` therefore
have their own ceiling in `firmware/config/settings.py` (`CHANNEL_DUTY_MAX`),
and the firmware refuses anything above `DUTY_MAX` for every channel not on that
list.

**`MAX_BURST_MS` still applies.** A held grasp releases after 4 s and needs 2 s
of rest, so you cannot hold an object indefinitely. That limit stays because the
finger flexors are the smallest muscles on the arm and fatigue fastest.

## Pose noise: measure it, do not guess

Vision output is noisy, and the deadband must **exceed** that noise or the
controller chases jitter. Measure before tuning:

`run.py` binds the pose port, so it cannot be running at the same time. Use the
**pose-only** mode, which starts the vision side without the controller:

```cmd
:: terminal 1 - vision + 3D twin, UDP 9090 left free. Leave this window open;
:: the twin opens in your browser so you can see the posture being held.
py tools\launch.py --pose-only

:: terminal 2 - activate the venv first
cd C:\Users\faisa\Desktop\juno_hack
.venv\Scripts\activate
py tools\pose_noise.py
```

Subject holds ONE still posture for the whole run; everything that moves is
noise. It reports per-joint standard deviation, outlier structure, peak implied
velocity, per-axis landmark noise, and models the **real** filter chain
(median then one-euro) on your data.

Each capture is written to `pose_capture.json` — **landmarks as well as
angles** — so filter settings can be re-evaluated without asking anyone to sit
still again:

```cmd
py tools\pose_noise.py --replay --median 9 --mincutoff 0.10 --beta 0.005
```

Label captures to compare physical setups instead of overwriting them:

```cmd
py tools\pose_noise.py --label frontOn
:: turn the subject or camera ~45 deg, hold the SAME posture, then
py tools\pose_noise.py --label camera45
```

### Read the "character" column before touching any filter

The tool classifies each joint as **white noise**, **mixed**, or
**DRIFT/MOVE**, by comparing the spread of frame-to-frame *differences*
against the spread of the signal itself. Independent samples make the
differences √2 times as wide as the signal; a slow wander makes them far
narrower.

This matters because **a low-pass filter can only remove the white part.** If a
joint reads `DRIFT/MOVE`, either the subject moved during the capture or the
estimator is drifting, and no amount of filtering will help — no cutoff value
removes it.

### A large recommended deadband is a measurement problem, not a setting

**You do not paste the recommended deadband into `settings.py`** — the
controller sizes its own from live noise (see below). Read it instead as a
score for your camera setup.

A deadband is dead travel: the controller ignores errors smaller than it, so the
arm stops that far short of every target. A 13° elbow deadband would mean an arm
that visibly never arrives. When the tool recommends something that large, fix
the measurement rather than accepting it.

The tool also prints **per-axis landmark noise**, which settles the question
directly: if the depth axis is more than ~1.5x the in-plane axes, the camera
angle is your problem and no filter competes with fixing it.

**When the elbow is the worst joint, suspect camera geometry first.** Elbow
angle is computed from the **wrist** landmark, and with a front-on camera elbow
flexion swings the forearm *toward the lens* — the depth axis, which is where
MediaPipe is weakest by a wide margin. Shoulder abduction, by contrast, is a
sideways in-plane motion, which is why it usually measures far quieter. So a
capture where abduction is quiet and the elbow is wild is a geometry signature,
not a filtering problem.

Cheap fixes, roughly in order of value per minute spent:

| Fix | Why it works |
|---|---|
| Turn the subject or camera **~45°** (three-quarter view) | Puts elbow flexion back in the image plane, where x/y landmarks are good. Costs nothing and can beat any filter. |
| Make sure the **whole forearm and hand** stay in frame | A wrist landmark that is clipped or occluded is guessed, not measured. |
| More light, plain background | Landmark confidence drops fast in dim or cluttered scenes. |
| Move the camera **back** and zoom in | Reduces perspective foreshortening along the depth axis. |
| Long sleeves off, contrasting clothing | Helps the estimator find the limb at all. |

Re-run the capture after each change and compare — that is the point of saving
`pose_capture.json`.

### Two-stage filter, because vision noise is two problems

| Noise | Looks like | Handled by |
|---|---|---|
| **Landmark jumps** | occasional large outliers | **median window** (`POSE_MEDIAN_WINDOW`) |
| **Jitter** | small, every frame | **adaptive low-pass** (`POSE_ONEEURO_*`) |

Order matters — median **first**. The adaptive stage reads a large fast change
as genuine motion and speeds up to follow it, so an outlier reaching it before
the median gets passed through rather than rejected.

### When outliers come in bursts, no filter helps

A median window rejects an outlier only while it is a **minority** of that
window. Median-5 handles one or two bad samples; a run of six *becomes* the
median and passes through as if it were signal.

A real capture showed exactly that on shoulder abduction — bursts up to 6
consecutive samples, single-frame steps of ~1000 °/s while the subject was
deliberately motionless, and only 43% noise reduction where the other joints
got ~60%. Those are **tracking dropouts**, roughly 200 ms each, where MediaPipe
briefly mis-locates the landmark. Both elbow and abduction excursed in the same
frames, which is what identifies it as a shared-landmark failure rather than
per-joint noise.

`pose_noise.py` reports the `burst` and `peak` columns for this. Two responses,
in order:

1. **Stop the estimator emitting them.** axon-main returns *no* landmarks below
   `MIN_LANDMARK_VISIBILITY` rather than a low-confidence guess — but the
   default 0.5 is permissive enough to let these through. Raise it:

   ```cmd
   py tools\launch.py --min-visibility 0.7
   ```

   This trades tracking coverage for correctness, which is the right trade
   here: a dropped frame ages out after `POSE_STALE_MS` and **stops
   stimulation**, whereas a confidently wrong frame gets acted upon.

2. **Cap the damage from what still gets through.** `POSE_MAX_RATE_DEG_S`
   (400 °/s) limits how far one sample can move a joint. It *limits* rather
   than discards, deliberately — the discard-and-hold version was measured and
   made the elbow **worse** (sd 1.42 → 2.33), because a genuine fast change is
   held for the whole retry window and then snaps, which is a bigger deviation
   than the noise it replaced. Limiting also guarantees convergence: if the
   estimator switches which arm it tracks, a rejecting gate would freeze the
   pose forever.

Physical causes worth ruling out first: arm resting against the torso (the
classic MediaPipe confusion), clothing that matches the background, dim light,
motion blur.

### Why the second stage is adaptive, not a fixed alpha

A fixed exponential filter has one constant asked to do two incompatible jobs.
Holding still, we want heavy smoothing, because the deadband must exceed the
residual noise. Moving, we want almost none, because lag here adds to a loop
already 150–300 ms slow. One alpha can only compromise.

The **one-euro filter** (Casiez, Roussel & Vogel, CHI 2012) makes the cutoff a
function of the signal's own speed: barely moving → cutoff drops and it smooths
hard; moving → cutoff rises and it gets out of the way. Since "still" and
"moving" are exactly when the two behaviours are wanted, the trade-off
dissolves rather than being split.

Measured on a real 553-sample held-posture capture at 27.6 Hz (elbow):

| Filter | sd | step lag | deadband it needs |
|---|---|---|---|
| raw | 2.51° | 0 ms | 7.5° |
| median-5 + EMA 0.35 *(previous)* | 1.70° | 145 ms | 5.0° |
| median-5 + EMA 0.10 | 1.14° | 398 ms | 3.5° |
| median-5 + one-euro 0.15/0.005 | 1.06° | 181 ms | 3.0° |
| **median-9 + one-euro 0.10/0.005** *(current)* | **best of the above on the noisiest capture** | **292 ms** | sized live |

The fixed filter could reach that noise level only by paying 398 ms of lag —
more than the rest of the loop combined. Set `POSE_FILTER_MODE = "ema"` to
restore the old behaviour.

**Tuning order:** set `POSE_ONEEURO_BETA = 0` and lower `POSE_ONEEURO_MINCUTOFF`
until a held posture is quiet enough, then raise `beta` until movement stops
feeling delayed. Check both against a saved capture rather than a person:

```cmd
py tools\pose_noise.py --replay --mincutoff 0.15 --beta 0.005
```

### The deadband is a fixed 3°, and noise is reported rather than acted on

An earlier version widened the deadband automatically to whatever the live pose
noise required. **That was wrong, and measurement is what showed it.**

The premise was "the deadband must exceed the noise or the controller chases
jitter". True when the controller collapsed its output to zero inside the band;
false once it started *holding* its learned duty there instead (see above).
Counting relay transitions over 25 s at the setpoint, with real noise injected:

| deadband | 0.5° | 1° | 2° | 3° | 5° | 8° | 12° |
|---|---|---|---|---|---|---|---|
| relay switches | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| steady error | 0.3° | 0.3° | 0.3° | 0.2° | 2.0° | 4.2° | 7.6° |

Zero chatter at every width, at both a quiet (1.1°) and a noisy (2.6°) feed.
Widening bought nothing and cost accuracy — and at 12° the arm never settled at
all, so every keypress smaller than the band appeared to do nothing. That is
what made the controls feel dead.

**The noise measurement is still taken and still reported.** It is a genuinely
useful early warning that the camera setup is drifting; it just no longer
silently changes how the arm behaves. `run.py` shows `noise:e4` when measured
noise exceeds a joint's deadband — the arm will hunt around its target rather
than sit on it, and the fix is the camera, not the gains.

Set `DEADBAND_ADAPTIVE = True` to restore the old behaviour if a rig ever does
chatter. It is capped at `DEADBAND_MAX_DEG = 6.0`, lowered from 12 because the
table above shows the arm stops settling well before that.

Two details that matter more than they look:

- **It measures the FILTERED stream, not the raw one.** The deadband exists to
  stop the controller reacting to what survives filtering. On real data raw
  noise was 5.2° where the filtered residual was 2.3°, so sizing from raw would
  have doubled the deadband for nothing.
- **It only updates while the joint is nearly still** (< 15 °/s). The baseline
  it measures against lags real movement, so during a commanded move the
  residual reflects filter lag rather than noise — feeding that in would widen
  the deadband exactly when the arm is trying to travel, and it would stop
  short of every target it was moving toward. Stillness is also when the
  deadband governs behaviour, so it is the right time to measure.

`run.py` shows `db:e5` **only when the deadband has risen above its floor**. If
that appears, the pose feed has degraded and the arm will settle further short
of its targets — which otherwise looks like weak gains and gets mis-debugged.

Ceiling is `DEADBAND_MAX_DEG = 12.0`: past that the feed is broken badly enough
that the operator should be told, not quietly accommodated by an arm that
ignores its targets.

### The twin's jitter and the controller's noise are separate paths

The 3D twin receives raw landmarks over its own WebSocket straight from
axon-main and smooths them itself for display; the controller receives the same
landmarks over UDP and filters its own copy. **Neither filter affects the
other.** So a visibly smoother twin does not mean cleaner control values, and
`pose_noise.py` is the only thing that tells you about the numbers actually
driving stimulation.

`twin.html` uses the same adaptive idea, expressed as a smoothing *rate* rather
than a cutoff (`POSE_SMOOTHING_MIN_RATE`, `POSE_SMOOTHING_BETA`). It is display
only — changing it cannot affect what the arm is told to do. Set
`POSE_ADAPTIVE_SMOOTHING = false` there to go back to the fixed rate.

An exponential filter *cannot* reject an outlier — it smears one across several
frames, which is worse for control than the spike itself, because the error
persists. A median window discards it outright: a single bad sample can never be
the median of an odd-sized window.

Measured on a simulated feed (1.2° jitter + a 25° jump every 37 frames):

| Filter | steady-state sd | worst error from a jump | frames to recover |
|---|---|---|---|
| raw | 4.36° | 26.3° | — |
| EMA only | 2.02° | 9.7° | 4 |
| **median + EMA** | **0.52°** | **1.5°** | **0** |

Cost is lag: the median adds about half its window (~35 ms at 28 Hz with n=5),
on top of the exponential stage. Acceptable here because the loop bandwidth is
only ~0.5–1 Hz. Keep the window **odd** and **small**.

### How to choose the deadband on hardware

Because the hold fix removed the chatter, the deadband can now be much tighter
than the original 5–6°. Measured with the hold behaviour in place, **relay
transitions stayed at 0 for every deadband down to 0.5°**:

| deadband | 0.5° | 1° | 2° | 3° | 5° |
|---|---|---|---|---|---|
| steady error | 0.48° | 0.91° | 1.89° | 2.88° | 4.75° |
| relay switches / 30 s | 0 | 0 | 0 | 0 | 0 |

Defaults are now **3°** (down from 5–6°). That is a compromise, not an optimum.

**The binding constraint on real hardware is pose-estimator noise, not the
relays.** The deadband must exceed the jitter in the incoming pose, or the
controller chases noise. With simulated Gaussian jitter the effect is visible —
and note that noise *dithers* the controller out of the band, which actually
drives the mean error toward zero:

| pose noise (sd) | deadband 1° | deadband 3° | deadband 5° |
|---|---|---|---|
| 0.5° | err 0.03° | err 1.40° | err 3.23° |
| 1.5° | err −0.06° | err 0.07° | err 0.67° |
| 3.0° | err −0.10° | err −0.03° | err 0.05° |

So: **measure your pose noise first**, then set the deadband to roughly 1–2×
its standard deviation. A noise-free simulation makes the offset look worse
than it will be in practice.

If you still need the last degree or two, bias the target up by roughly one
deadband rather than shrinking the band into the noise floor.

### The `i_limit` gotcha

`i_limit` bounds the integral's **contribution to the output in duty units**,
not the raw accumulator. This matters: to hold against gravity, the integrator
alone must be able to supply the full holding duty, so `i_limit` must be ≥ the
duty needed to hold the heaviest pose (hence ~`DUTY_MAX`).

Bounding the raw accumulator instead makes the effective limit depend on `Ki`,
which silently cripples the controller — with `Ki = 0.004` and a raw cap of 12,
the integrator could only ever contribute `0.048` duty and the arm never
reached its target. This bug was caught by `test_simulation.py`.

---

## Force modulation: burst PWM on relays

We cannot set stimulation amplitude in software — the AS8016's intensity is set
by hand. Instead we gate each channel on and off, and the **muscle low-passes
it**, so average force tracks duty cycle.

Mechanical relays constrain the carrier:

| Quantity | Value | Consequence |
|---|---|---|
| Relay close / open | ~10 ms / ~5 ms | clean switch ≈ 20–30 ms |
| `PWM_PERIOD_MS` | 150 | ~6.7 Hz carrier |
| `MIN_PULSE_MS` | 25 | ~6 usable duty steps |

At 6.7 Hz, **force visibly ripples** — the muscle does not fuse it into a smooth
contraction. What saves the demo is that **limb inertia low-passes position**
even when force ripples, and proximal joints (shoulder, elbow) smooth best.

A **PhotoMOS/SSR** would allow 20–50 Hz and genuinely smooth force. That is the
single highest-value hardware upgrade available; nothing in the software would
need to change beyond `PWM_PERIOD_MS`.

Pulses shorter than `MIN_PULSE_MS` are **dropped entirely** rather than issued —
a half-actuated relay buzzes without delivering useful force.

### The actuator has a dead zone at the bottom — compensate for it

That dropping rule has a consequence that is easy to miss: with
`MIN_PULSE_MS = 25` and `PWM_PERIOD_MS = 150`, **every duty below 25/150 =
0.167 produces no relay movement at all.** Commanding 0.10 and commanding 0.0
are physically identical.

So the usable actuator range is **0.167 – 0.70**, not 0 – 0.70. A controller
unaware of this silently does nothing whenever it asks for a small effort.

This is not hypothetical: in hardware-in-the-loop the simulated arm needed only
**0.08–0.11** to hold position, so *nothing ever fired* while every layer
reported success — packets sent, board armed, duties computed. It looked like a
dead control path and was in fact correct arithmetic meeting a physical limit.

`mapping.efforts_to_duties()` therefore snaps requests out of the dead zone
(`MIN_EFFECTIVE_DUTY` / `DEADZONE_COMPENSATION` in `controller/settings.py`):

| Requested | Commanded | Why |
|---|---|---|
| 0.00 | 0.00 | idle |
| 0.02 | **0.00** | below half-threshold: a twitch is not worth it |
| 0.10 | **0.167** | snapped up so it actually fires |
| 0.50 | 0.50 | unchanged |
| 5.00 | 0.70 | still clamped by `DUTY_MAX` |

**Keep `MIN_EFFECTIVE_DUTY` in sync with the firmware.** It is derived from
`MIN_PULSE_MS / PWM_PERIOD_MS`; change either on the board and this must follow.

A PhotoMOS/SSR would shrink the dead zone dramatically — `MIN_PULSE_MS` exists
because a *mechanical* relay needs ~25 ms to transition.

---

## Joint → muscle mapping

Signed effort routes to exactly one muscle of an antagonist pair:

| Effort | Elbow | Shoulder flex | Shoulder abduction |
|---|---|---|---|
| positive | CH1 biceps | CH3 ant. deltoid | CH5 mid deltoid |
| negative | CH2 triceps | CH4 post. deltoid | *(gravity)* |

**Never co-contract.** Driving both sides of a pair wastes current, fatigues the
subject quickly, and can lock the joint solid.

**Abduction is one-directional** by design — see [`SAFETY.md`](SAFETY.md) for
why there is no adductor channel.

---

## Timing budget

| Stage | Delay |
|---|---|
| Pose estimation + transport | 30–100 ms |
| Control + link | ~10 ms |
| Electromechanical delay (stim → force) | 50–100 ms |
| Relay + PWM phase | up to 150 ms |
| **Total** | **~150–300 ms** |

That caps usable closed-loop bandwidth at roughly **0.5–1 Hz**, which is why
gains are low and settling takes seconds. Trying to tune for a snappy response
against 250 ms of dead time produces oscillation, not speed.

**Target accuracy: ±5–10°, settling in 2–4 s.** Promise that, not precision.

---

## Tuning procedure

Gains live in `controller/settings.py`, in the `GAINS` table:

```python
GAINS = {
    #                 Kp      Ki    deadband_deg  i_limit(duty)
    "elbow":         (0.020, 0.030, 5.0, 0.70),
    "shoulder_flex": (0.016, 0.024, 6.0, 0.70),
    "shoulder_abd":  (0.016, 0.024, 6.0, 0.70),
}
```

Safety limits live in `firmware/config/settings.py`. Editing gains here can make
the demo sloppy; it cannot make the hardware unsafe.

**1. Sanity-check in simulation first.** (Venv active — see the
[README](../README.md#0-set-up-the-virtual-environment-once).)

```bash
cd controller
python run.py --sim
```

**2. Find the subject's intensity level** by hand on the TENS unit (lowest
level upward, to a clear but comfortable contraction), then map duty to angle:

```bash
python tools/calibrate.py --channel 1 --joint elbow --manual
```

**3. Tune one joint at a time**, editing `GAINS` and restarting `run.py` between
attempts:

| Symptom | Change |
|---|---|
| Approaches the target too slowly | increase `Kp` by ~50% (e.g. 0.020 → 0.030) |
| Overshoots and oscillates | decrease `Kp` by ~30% |
| Stops short and stays short (droop) | increase `Ki` by ~50% |
| Slowly drifts past the target, then back | decrease `Ki` |
| Relays buzz once it arrives | increase `deadband_deg` (5 → 8) |
| Reaches target then sags over ~10 s | increase `i_limit` toward `DUTY_MAX` |

**4. Never add a D term.** If it oscillates, lower `Kp`, then `Ki`, then widen
the deadband — in that order.

Verify each change against the acceptance criterion: **±5–10°, settling in
2–4 s, holding without chatter.**

---

## Grip

Grip is **triggered, not servoed**: the pose estimator tracks shoulder/elbow/
wrist, not fingers, so there is no feedback to close a loop on. Pressing the
grip key drives CH7 (finger flexors) at `DUTY_MAX` and holds CH8 released.

Fire grip **after** the arm has settled into pose — the finger flexors originate
near the elbow, so a strong grip adds a little elbow-flexion torque that the
elbow loop then has to absorb.
