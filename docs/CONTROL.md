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
