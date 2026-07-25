# Closed-Loop Arm Control — Technical Feasibility Memo

**Question:** Can we build a feedback controller that drives a passive subject's arm to pre-selected poses using our AUVON AS8016 (relay-actuated) + two 9-DOF IMUs?

**Verdict:** Yes, for a scoped 1–2 DOF demo. The sensing and control-theory sides are straightforward; the **actuator (a consumer massage-TENS driven by button relays) is the bottleneck**, and the demo hinges on changing *how* we actuate — using the channel relays as a PWM/duty-cycle command rather than pressing the intensity buttons.

---

## 1. What the two reference papers actually do (and why our task is harder)

| | LibreMano (Sensors 2025) | MES-FES orthosis (Assistive Tech 2024) | **Our demo** |
|---|---|---|---|
| DOF | 1 (hand open/grasp) | 1 (grasp) | 1–2+ (elbow, shoulder) |
| Loop closed by | **The user** (volitional EMG + proprioception) | **The user** (EMG trigger) | **Our controller** (subject is blindfolded/passive) |
| Stimulator | Custom current-controlled, biphasic PW=300 µs, T=60 ms doublets, ~15 mA | Custom pulse-train, H-bridge + step-up | AUVON AS8016 via button/channel relays |
| Control variable | EMG amplitude → stim intensity (proportional) | EMG threshold → on/off | Duty cycle / intensity level |

Both papers are **myoelectric (human-in-the-loop)**: residual EMG scales or triggers the stim and the person's own nervous system closes the loop. Our blindfolded, passive subject removes the human from the loop, so **our machine has to close it entirely** — this is autonomous FES position control, closer to the classic 1990s–2000s FES elbow/knee angle-control literature than to these two papers. Established, but it needs a real controller.

**Useful parameter to steal from LibreMano:** biphasic **300 µs pulse width, ~16 Hz (60 ms) doublet** stimulation sits at the low end of the tetanic-contraction threshold → sustained contraction with minimal fatigue. Pick the AUVON mode whose behaviour is closest to this.

---

## 2. Our control authority (what the AUVON actually gives us)

Device: 4 channels (A1/A2/B1/B2), 24 modes (P1–P16 TENS, **P17–P24 EMS = muscle contraction**), 20 intensity levels, 10–90 min timer.

Three hard constraints from the manual:

1. **Modes are pre-programmed time-varying envelopes**, not steady output. Even at fixed intensity the force ramps/pulses/pauses (e.g. "shallow to deep with 3–4 s pause"). We do **not** get a clean "command → force" actuator out of the box.
2. **Intensity = 20 coarse discrete steps** via *relative* up/down presses. Slow and quantized → limit-cycle/oscillation risk if used as the live control variable.
3. **Intensity resets to the lowest level on every mode change.** ⇒ Mode must be fixed before the run; it cannot be a live control variable.

Net live control variables per channel: **{channel ON/OFF (fast, via relay), intensity level 0–20 (slow, via buttons)}**.

---

## 3. The key idea: PWM the channel relays, don't press the buttons

Instead of servoing the 20-step intensity, **fix the mode and a base intensity that produces a strong tetanic contraction, then modulate force by gating the channel on/off (PWM / burst-duty-cycle) at ~5–20 Hz.**

- Skeletal muscle is a natural **low-pass filter** → average force ∝ duty cycle → a **fast, near-continuous command variable**.
- Sidesteps all three constraints above (no button quantization, no mode reset, much lower latency).
- Drive the fast gating through the **MOSFET path (IRLZ44N)** behind the galvanic-isolation relays — mechanical relays are too slow (~5–15 ms) and will wear out under kHz-scale cycling. Keep the existing isolation architecture.
- Caveat: the chosen mode's own envelope convolves with your duty cycle, so **pick the most "constant" EMS mode** (empirically characterize P17–P24 force-vs-time at fixed intensity first).

Use the 20-step intensity **once** to set operating range per subject; use **relay PWM** for closed-loop control.

---

## 4. Sensing & state estimation (this part is easy)

- Two 9-DOF IMUs → forearm + upper-arm segments.
- **Relative orientation between the two IMUs = elbow flexion angle** (clean, our best-controlled DOF). Single-IMU absolute orientation (gravity + mag) = shoulder/upper-arm orientation.
- Fusion: **Madgwick or Mahony** filter (or EKF) per IMU; calibrate IMU-to-segment alignment with a known reference pose at start.
- Controllable "pose" = **elbow angle (1 DOF)** + **shoulder elevation/orientation (1–2 DOF)**. Wrist is out with only 2 IMUs.
- Watch for gyro drift + magnetometer distortion near the stimulator/metal; keep mags away or disable mag and accept slow yaw drift over a short demo.

---

## 5. Recommended controller

Keep it simple — delays and quantization make high-order control counterproductive.

1. **Per-subject calibration (≈2 min):** ramp duty cycle 0→100 % on each agonist, record steady elbow angle → recruitment curve. Gives a **feedforward** map (target angle → nominal duty cycle) and the activation threshold.
2. **Feedback:** discrete **PI** on angle error → duty-cycle trim, with **anti-windup** (essential — actuator saturates) and **integral action to absorb fatigue** (plant gain drifts down over seconds). A **bang-bang-with-hysteresis** fallback is even more robust if PI tuning is fussy.
3. **Antagonist / return:** prefer **gravity-assisted** poses (seated, elbow flexes up against gravity, relaxes down). Only add an antagonist channel (triceps) if a pose needs active extension.
4. **Feedforward + feedback** combined = practical accuracy without chasing bandwidth.
5. **Safety (already in our stack):** watchdog auto-off on no-update/latch-up (LibreMano explicitly flags stim latch-up as an instability mode), capped burst duration, current never crosses chest, physical kill switch held by subject.

Realistic performance target: **±5–10° steady-state, 2–4 s settling per pose.** Sell that, not surgical precision.

---

## 6. Feasibility scorecard

| Element | Verdict | Note |
|---|---|---|
| IMU joint-angle feedback | ✅ Solid | Standard fusion; elbow angle is clean |
| Control theory / loop closure | ✅ Solid | Simple PI/bang-bang sufficient |
| Actuator: intensity buttons | ⚠️ Weak | Coarse, slow, resets on mode change — avoid as live variable |
| Actuator: relay PWM path | ✅ Enabling | Turns coarse device into a graded, fast command |
| Muscle selectivity (surface) | ⚠️ Limited | Co-contraction/spillover; limits DOF count |
| Fatigue / plant drift | ⚠️ Manageable | Integral action + short runs |
| Passive/random subject | ⚠️ Risk | Response varies person-to-person — see §7 |

---

## 7. Demo risks & rehearsal checklist

- **Subject variability is the #1 demo risk.** A random blindfolded judge may respond weakly or need higher (uncomfortable) current. **Mitigation:** briefly pre-screen the judge before going live, or run the driven arm on a **pre-characterized teammate** and let the judge *feel/observe*. Have a "hero subject" identified in rehearsal.
- **Characterize all EMS modes first** — pick the one with the flattest sustained contraction; log force/angle vs duty cycle.
- **Electrode placement precision** drives everything — mark positions, use an applicator/splint to stabilize and cut motion artifacts.
- **Envelope quiver:** the mode's internal pulsing may make the arm visibly tremble — either pick a smoother mode or frame it as "muscle activating."
- **Latency budget:** EMD ~50–100 ms + envelope + inertia → keep loop expectations modest; pose-to-pose transitions of a few seconds look intentional and controlled.
- **3D digital twin:** drive it live from the same IMU stream so judges see target-pose vs actual-pose tracking — this makes the closed loop *legible* even when the arm is only roughly there.

---

## 8. One-line answer for the pitch

> "Our sensing and control loop are textbook; our innovation is making a $40 consumer stimulator into a controllable actuator by PWM-gating its channels — so we can servo a passive arm to a target pose from IMU feedback alone."

*Sources: AUVON AS8016 manual (device constraints); LibreMano neuroprosthesis, Sensors 2025 (stim parameters, latch-up/instability); MES-FES orthosis, Assistive Technology 2024 (EMG-triggered architecture).*

---

# Addendum — Hardware build decisions (v2)

## A. Turn-on jolt: cause & root-cause fix

**Cause:** the output is a constant-*current* source. With the channel relay open, it drives an **open circuit**, so its voltage runs up to the **compliance limit (~100 V+)** and charges the output/coupling capacitor. Closing the relay dumps that stored charge into the skin as one spike → the jolt. It scales with intensity level (higher level → higher compliance voltage → bigger dump) and it's **capacitive** (occurs on *connect*), not inductive.

**Fix:** never present the device with an open circuit — keep a dummy load across the output whenever the electrode is disconnected.

## B. Dummy-load wiring (SPDT relay, per channel)

Each channel has two output terminals A & B; the body sits between them; the relay interrupts the A-side leg.

```
Device A ── COM
              ├─ NO ── active electrode ─( body )─ return electrode ── Device B
              └─ NC ── R ── Device B          ← dummy leg
```

- **Energized (stimulating):** COM→NO → current A → body → B; R idle.
- **De-energized (off):** COM→NC → R sits **across A–B**, clamps the compliance voltage, output never floats → no jolt.
- The resistor's free end goes to **terminal B** (the channel's other output), **not back to COM** — tying it to COM shorts it out in the off state and does nothing.
- **R ≈ body/electrode impedance (~1 kΩ)** so the source settles to a similar voltage in both states.
- Dummy load must be **differential (across the two terminals)**. Do **NOT** tie a leg to system/battery ground — the stim output is floating/isolated; grounding it breaks isolation and is a safety hazard.
- Bench check: confirm the relay rests on **NC** when de-energized; scope A–B in the off state → voltage should stay clamped at ~I·R instead of climbing.
- Mechanical SPDT is **break-before-make** → ~1 ms open "blink" during switching; harmless at pose-change rates.

## C. Fast gating switch (for PWM force control)

- Mechanical relays are fine for the connect/disconnect above, but **too slow (~5–15 ms, and they wear)** for PWM duty-cycle modulation.
- For PWM use a **bidirectional, isolated switch rated ≥150–200 V**:
  - **Best: opto-isolated PhotoMOS / MOSFET SSR** — internally back-to-back MOSFETs, so bidirectional; handles mA-level currents; isolation + gate drive come for free.
  - Alt: **two high-voltage MOSFETs back-to-back** (source-to-source, ~200–400 V) with **isolated** gate drive.
- **Do NOT** use a single N-MOSFET (its body diode conducts the reverse phase of the biphasic pulse) and **not the IRLZ44N** on the output (55 V < compliance). **IGBT is also wrong here:** still unidirectional, poor at mA currents, ~2 V knee.
- The output is **bipolar + high-voltage + low-current** → PhotoMOS is the natural fit.

## D. Grip channel

- **Site:** wide active pad on the **volar (palm-side) proximal forearm** over the finger flexors (FDS/FDP) + a return pad distally → gross power grip. This is a full **channel** (needs both pads), not a single pad.
- **Scope:** general **power grasp** (all fingers curl, robotic-gripper style) — **not** individuated fingers or thumb opposition.
- **Control: open-loop / triggered.** The two arm IMUs can't see the fingers close, so grip is feed-forward: stimulate → close; cut stim → relax open (add a dorsal-forearm extensor channel if you want an active open).
- **Independence:** the AUVON has only **2 independent amplitude banks** (A1/A2 shared, B1/B2 shared). Get independent shoulder / elbow / grip via **per-output PWM gating**, not separate amplitudes — set bank amplitude high, then duty-cycle each output.
- **Cross-talk:** forearm flexors originate near the elbow → grip adds slight elbow-flexion/pronation torque. Fire grip **after** the arm settles into pose; the elbow loop absorbs the rest.

## E. Demo sequence & DOF map

**Sequence:** reach to pose (closed-loop, IMU feedback) → trigger grip to close on an object → release. Mirrors how real grasp neuroprostheses are used.

| DOF | Control | Feedback |
|---|---|---|
| Elbow flexion (primary) | Closed-loop PI on angle → duty cycle | IMU relative orientation |
| Shoulder elevation/orientation | Closed-loop (coarser) | IMU absolute orientation |
| Power grip | Open-loop / triggered | none (optional finger flex/FSR later) |

## F. Relay-only closed-loop control (no PhotoMOS)

**Actuator reality:** clean repeatable relay switch ~20–30 ms → usable software-PWM carrier **~6–8 Hz** (period ~150 ms), giving ~5–7 duty steps. Force ripples at that rate, but **limb inertia + gravity + a slow feedback loop smooth the *position*** — proximal segments (upper arm/elbow) filter best. Don't fine-PWM the fingers; keep grip triggered.

**Two nested loops:**

- *Inner (force):* software PWM on the relay GPIO. Period ~150 ms, min pulse ~25 ms (one clean transition). Duty = command from the outer loop.
- *Outer (position):* discrete **PI on IMU joint angle → duty**, updated once per PWM period. Gravity provides the return; one agonist channel per DOF.
- Keep gains **low**: total loop delay (relay + PWM + 50–100 ms EMD + IMU) ≈ 150–250 ms → outer bandwidth ~0.5–1 Hz → settle in seconds (matches "rough"). A **±5° deadband** stops switching at the setpoint (holds still, saves the relay, looks clean). **Anti-windup** on the integrator (actuator saturates 0/100 %).

**Safety envelope — INDEPENDENT of the control loop (never rely on the PI loop for safety):**

1. **Device intensity level hard-capped** at a pre-set comfortable max. The AUVON is a constant-*current* source, so this **physically bounds peak current** — PWM only lowers the average, never exceeds the cap.
2. **DUTY_MAX ≤ ~70 %** so the muscle always rests (fatigue + safety).
3. **Max continuous on-time per burst + forced cooldown** (carry over the ~300 ms burst rule).
4. **Watchdog:** no fresh control update within 500 ms → all relays open (fail-safe off).
5. **Joint-angle ROM limits:** IMU angle outside safe range → cut stim.
6. **Dummy-load** across the output (Addendum B) keeps compliance voltage clamped — no spikes.
7. **Physical kill switch** in series, held by subject; current path one-limb, **never across the chest**. Start low; test on consenting teammates with no contraindications first.

**Bench-verify before the run:**

- Scope GPIO command vs actual contact closure → find your true clean PWM ceiling (module opto/flyback may slow release).
- Measure intensity-level → steady-angle curve per subject (feedforward seed).
- Confirm the deadband actually stops chatter at the setpoint.

**Pseudocode (per DOF):**

```
loop every PWM_PERIOD (~150 ms):
    angle = fuse(imu_upper, imu_fore)
    if angle outside [ROM_MIN, ROM_MAX]:        # safety, independent of PI
        relay_off(); pet_watchdog(); continue
    err = target - angle
    if abs(err) < DEADBAND:
        duty = HOLD_DUTY                         # or 0 if gravity holds the pose
    else:
        integ = clamp(integ + err, -IMAX, IMAX) # anti-windup
        duty  = clamp(Kp*err + Ki*integ, 0, DUTY_MAX)   # DUTY_MAX <= 0.70
    on_time = duty * PWM_PERIOD
    if on_time >= MIN_PULSE:
        relay_on(on_time); relay_off(PWM_PERIOD - on_time)
    else:
        relay_off(PWM_PERIOD)
    pet_watchdog()
```

Grip runs on its own channel as a **triggered on/off**, fired *after* the arm settles into pose — not through this PI loop.
