# Build Context — Closed-Loop FES Arm Controller (v2)

Paste this into a new session as context. It describes a closed-loop control system we need to build. Read it fully before proposing code or architecture.

## What we're building (goal)

A **closed-loop controller that teleoperates a human arm using surface functional electrical stimulation (FES)**. There is **no intent sensing**. For the demo, an operator drives the arm with the **keyboard** (the *target*); the arm's **current pose is supplied to us over an API by a teammate's pose-estimation service** (the *feedback*). **We do NOT implement vision/pose estimation** — we build the API that ingests poses, then do all the control and stimulation. We treat the human arm exactly like a **robotic manipulator**: consume measured joint angles, command target joint angles, drive the muscles to reach them, correct against the incoming pose.

**Demo teleoperation (arrow keys):**
- **Left / Right** → move the hand left/right (via shoulder + elbow).
- **Up / Down** → move the hand up/down.
- **Hold Shift** → close the hand (grip).
- **X** → **emergency stop:** immediately zero all channels and **latch OFF** (requires a deliberate re-arm to resume — see Safety).

Rough accuracy is fine (±5–10°, settle in a couple of seconds). Later product goal: one real physiotherapy exercise a clinician prescribes, run as a daily recovery session from our app (out of scope for the demo build, but design so a scripted joint-trajectory can replace the keyboard input).

## Hardware (fixed — design around it)

- **MCU:** **Goouuu Tech ESP32-S3-N16R8** (16 MB flash, **8 MB *octal* PSRAM**). Because PSRAM is octal, **GPIO26–37 are reserved for flash/PSRAM — do not use them.** Also avoid strapping pins (0, 3, 45, 46), USB (19, 20), and UART0 (43, 44). Safe output GPIOs include: **1, 2, 4–18, 21, 38–42, 47, 48** (skip whatever your board wires to its onboard RGB LED, often 48/38). Any safe pin can do PWM via the ESP32-S3 LEDC/MCPWM matrix.
- **Stimulators:** **two** AUVON AS8016 4-channel TENS/EMS units → **8 stimulation channels total**, one per muscle group. We do NOT have a programmable stim API — we gate each channel's electrode leads with a relay and set mode/intensity once via the unit's buttons.
  - **Gotchas (per unit):** intensity is **relative** button presses, **20 levels**, and **resets to lowest on any mode change** → fix the mode before a run. The 24 modes are **time-varying envelopes**; pick the EMS mode (P17–P24) with the flattest sustained contraction empirically. Intensity is grouped into **2 banks per unit** (A1/A2, B1/B2) → 4 independent amplitude levels across both units; get per-muscle independence from **per-channel PWM gating**, not amplitude.
- **PWM relays (muscle gating):** **TONGLING JQC-3FF-S-Z** (4-relay module) + **SONGLE SRD-05VDC-SL-C** (4-relay module) = **8 relays for 8 muscle groups**. Both are **mechanical**, 5 V coils, opto+driver on the modules, GPIO supplies the logic trigger. **5 V supply available** for the coils (do not power coils from the board; share ground with the ESP32; keep the stim output floating/isolated).
  - **These are mechanical → slow:** ~10 ms close / ~5 ms open, clean repeatable ~20–30 ms. So force PWM is coarse (**carrier ~6–8 Hz, period ~150 ms, min pulse ~25 ms**); the limb's inertia + reciprocal muscles smooth the *position*. (No PhotoMOS available; relay-only is the plan.)
- **Timer keep-alive relay:** one **HK4100F-DC3V-SHG** (**3 V coil**) wired across the **timer button of *both* TENS units**, on **GPIO2 (G2)**. Pulse it once every ~5 min to stop the 20-min auto-off. *Note:* a 3 V coil can't be driven directly from a 3.3 V GPIO current-wise — drive it through a transistor + flyback diode (or a small module). **Verify on the bench that this button press actually resets the auto-off countdown and does NOT change the mode/intensity or cycle the timer duration** — if it does, find the correct keep-alive press.
- **Feedback: an external pose API (no IMUs, no vision on our side).** A teammate's service estimates **shoulder, elbow, wrist in 3D** and sends it to us; we consume/compute the **elbow angle** and the **two shoulder angles** (shoulder as a 2-DOF spherical joint referenced to the torso). We define and build the ingest API (see Comms). Treat incoming poses defensively: **low-pass filter** the angles (vision is jittery), and if the pose goes **stale/drops out** (no update within a timeout) → **hold or cut stim** (safety), never keep driving on an old pose.
- **Electrodes:** surface pads over agonist/antagonist muscle groups (below).

## Control architecture

The camera + vision live on a **PC**, so the loop closes on the PC. The ESP32 is the **actuator + independent safety layer**.

```
[keyboard teleop] --JOINT-SPACE jog (no IK)--> target joint angles -->
[per-joint PI controller] --per-channel duty / on-off--> (UDP/serial) --> [ESP32-S3]
        ^                                                                     |
        |                                                         software PWM -> relays
   measured joint angles (filtered)                                          |
        |                                                              FES channels fire
[our pose-ingest API] <--estimated pose-- [teammate's pose service] <-- subject's arm moves
```

- **Kinematics — AS BUILT: joint-space, no IK.** The arm is modelled as 3 DOF (shoulder flex + shoulder abduction + elbow), but the teleop keys jog **joint targets directly**; there is no Cartesian/end-effector control and no Jacobian. The only elbow/shoulder "coupling" is a hardcoded ratio in the key handler (`UP` = flex +3.0°, elbow +1.5°) chosen to look like a natural reach. Below that the three joints are **independent SISO PI loops**.
  - *Why not IK:* Cartesian jogging needs a damped-least-squares Jacobian inverse plus singularity handling (the arm straightens → Jacobian loses rank → targets diverge). Against ~150–300 ms of dead time, ~6 usable PWM duty steps, and coarse surface-FES muscle selectivity, that precision cannot be executed — it would invert geometry far more accurate than the actuator. Joint space is honest about the hardware.
  - `kinematics.forward_kinematics()` exists but is **not used** by the control path; only the pose→angle direction is live. Treat FK as scaffolding for a future Cartesian mode or 3D display.
  - Joint limits (ROM) are enforced on every jog.
- **Per-joint controller: discrete PI on joint angle → duty cycle.** PI **not** PD (integrator needed to hold against gravity and auto-compensate the unknown, fatiguing muscle gain; D omitted — noisy vision-derived angle + large dead time + over-damped plant). Include **anti-windup** (output saturates 0…DUTY_MAX) and a **±5° deadband** (stops switching at target). Optional **feedforward** from a per-subject calibration (duty→steady angle).
- **Reciprocal agonist/antagonist control (8 channels — use them):** each DOF has an agonist + antagonist so motion is active in both directions (not gravity-only). **Never co-contract** — drive one side at a time, neither near the deadband. Full wiring in **Muscle → Channel Map** below.
- *Reality check:* the **elbow (biceps/triceps) is clean**; the **deltoid heads are superficial** so shoulder flex/ext/abduction are targetable but co-activate somewhat; **shoulder adduction is the weak/risky one** (its muscles are on the chest — avoid). Plan the demo to lean on elbow + shoulder flex/abduct + grip.
- **Keep gains LOW.** Loop delay ≈ vision (30–100 ms) + comms + EMD (50–100 ms) + relay/PWM ≈ 150–300 ms → outer bandwidth ~0.5–1 Hz → settle over seconds. Matches slow keyboard jogging.
- **Grip = triggered ON/OFF** (Shift), a general power grasp, not through the PI loop; fire after the arm settles.

## Muscle → Channel Map (8 channels = 2 AUVON units × 4)

Each channel = **2 pads**, placed **longitudinally along the muscle belly** over the motor point, a few cm apart. Prep skin (clean + conductive gel); mark good positions once found.

| CH | Unit·Bank | Muscle | Joint · DOF | Movement | Role | Electrode placement |
|----|-----------|--------|-------------|----------|------|---------------------|
| 1 | U1·A | Biceps brachii / brachialis | Elbow · flex/ext | Bends elbow (hand up) | Agonist | Anterior upper arm: proximal belly + near elbow crease |
| 2 | U1·A | Triceps brachii | Elbow · flex/ext | Straightens elbow | Antagonist | Posterior upper arm, long/lateral head |
| 3 | U1·B | Anterior deltoid | Shoulder · flex/ext | Lifts arm forward/up | Agonist | Front of shoulder over ant. deltoid |
| 4 | U1·B | Posterior deltoid | Shoulder · flex/ext | Pulls arm backward | Antagonist | Back of shoulder over post. deltoid |
| 5 | U2·A | Middle (lateral) deltoid | Shoulder · abduction | Raises arm out to side (**gravity returns it / adduction**) | Agonist | Lateral shoulder, mid-deltoid bulge |
| 6 | U2·A | **SPARE** (unused) | — | — | — | Adduction handled by gravity; channel free |
| 7 | U2·B | Finger flexors (FDS/FDP) | Hand · grip | Closes hand (grip) | Agonist | Volar (palm-side) proximal forearm |
| 8 | U2·B | Finger/wrist extensors | Hand · release | Opens hand | Antagonist | Dorsal proximal forearm |

- **Shoulder abduction (CH5):** middle deltoid actively lifts the arm out to the side; **gravity returns it (adduction)** — no adductor channel driven. (Avoid the natural adductor: pectoralis major = chest electrodes near the heart, forbidden.) **CH6 is a free spare** — leave unused, or later use for a second grip/wrist pad or an abduction-assist muscle.
- **Bank grouping:** each AUVON has 2 shared-amplitude banks (A1/A2, B1/B2). Grouped by muscle size — arm on Unit 1; shoulder + forearm split on Unit 2 so the small forearm muscles (CH7/8) get their own low-amplitude bank. Per-muscle independence comes from **PWM gating**, not amplitude.
- **Controller mapping:** elbow-angle error → CH1/CH2; shoulder-flexion error → CH3/CH4; shoulder-abduction error → **CH5 to lift, gravity to lower** (one-directional agonist, no antagonist channel); grip (Shift) → CH7 on, CH8 for active release. CH6 unused.

## Safety (MUST be independent of the control loop, enforced on the ESP32)

1. **Hard-cap the AUVON intensity level** at a comfortable max (device is constant-*current* → this bounds peak current; PWM only lowers the average).
2. **DUTY_MAX ≤ ~70%**; **max burst on-time + forced cooldown** per channel.
3. **Watchdog on the ESP32:** no fresh command from the PC within 500 ms → **open all relays** (fail-safe off). This is critical since the control loop is on the PC.
4. **Joint-angle ROM limits** → cut stim if out of range.
5. **Dummy-load** so no stim channel is ever open-circuit (open circuit charges to compliance voltage and dumps a jolt on reconnect) — resistor (~1 kΩ) on each channel relay's **NC** to the channel's **other output terminal**; keep stim output **floating**, never grounded.
6. **Physical kill switch** in series held by the subject; current path **one-limb, never across the chest**.
7. **Operator e-stop (X key):** immediately zeroes all duties, sends an **explicit ALL-OFF kill packet**, and **latches OFF** until a deliberate re-arm. Treat this as a *convenience* layer only — it depends on the PC/app/link. It also naturally trips the watchdog (item 3) since the loop stops sending. **Recommended:** also wire a **physical e-stop button to a spare ESP32 GPIO (interrupt) that opens all relays independent of the PC.**

**Safety hierarchy (primary → convenience):** subject's in-line physical kill switch → ESP32 watchdog / hardware e-stop (PC-independent) → operator X-key e-stop. The keyboard kill never counts as a primary safeguard.

## What to build

1. **Control application (Python recommended):**
   - **Pose-ingest API** (this is a core deliverable): receive estimated poses from the teammate's service → filter → **elbow + 2 shoulder angles** (feedback). Handle stale/dropped poses safely.
   - Keyboard teleop → **joint-space jog** → target joint angles (no IK; see Kinematics above).
   - **Per-joint PI controllers** (anti-windup, deadband, optional feedforward) → per-channel duty / grip trigger.
   - Comms to ESP32; live target-vs-actual visualization.
2. **ESP32-S3 firmware (Arduino/PlatformIO C++ recommended for real-time; MicroPython acceptable):**
   - UDP/serial command receiver (per-channel duty + grip).
   - Non-blocking **software PWM** → 8 muscle relays; the **timer keep-alive** on GPIO2 (~5-min pulse).
   - **Safety enforcement:** duty cap, burst/cooldown, watchdog, all-off on fault.
3. **Comms contracts:**
   - **Inbound pose API (teammate → us)** — define this. Suggested: `{ shoulder:[x,y,z], elbow:[x,y,z], wrist:[x,y,z], timestamp }` (3D joint positions) OR pre-computed `{ elbow_angle, shoulder_flexion, shoulder_abduction, timestamp }`. Agree units/frame with the teammate. Transport: UDP or HTTP/WebSocket. Every message timestamped so we can reject stale poses.
   - **PC → ESP32:** `{ duty[8], grip:bool, kill:bool, seq }` at ~20–50 Hz. `kill:true` (or a dedicated kill packet) → ESP32 opens all relays immediately, latched off until an explicit re-arm.
   - **ESP32 → PC:** `{ state, fault, last_seq }` heartbeat.
4. **Per-subject calibration routine:** ramp duty per muscle, log steady angle → recruitment curve (feedforward + threshold).

## Bench-tuning parameters (determine empirically)

- Best EMS mode + base intensity per unit; the true clean relay PWM ceiling (scope GPIO vs contact).
- Camera placement / calibration / joint-angle filtering.
- PI gains, IMAX, DUTY_MAX, deadband, PWM period, loop rate.
- Confirm the timer keep-alive press behavior (resets countdown, nothing else).

## Explicitly out of scope

No intent detection, no EMG, no BCI. **No vision/pose estimation — that's a teammate's service; we only ingest its poses via our API.** Targets come from the keyboard (demo) or a scripted exercise trajectory (future app). No individuated finger control — grip is a gross power grasp.
