# Safety

This system drives electrical current through a person's muscles to move their
limb without their volition. Treat every section here as mandatory.

---

## 1. The safety hierarchy

Layers are listed from **most trusted to least**. Never let a lower layer be
the only thing standing between the subject and an unwanted contraction.

| # | Layer | Survives | Notes |
|---|-------|----------|-------|
| 1 | **Subject's in-line kill switch** | everything | Physical switch in the electrode lead, held by the subject. Works if every computer is dead. |
| 2 | **Hardware e-stop button** (GPIO IRQ) | PC crash, app hang, link loss | Optional but strongly recommended. Normally-closed to GND: a cut wire also kills. |
| 3 | **Firmware watchdog** (500 ms) | app hang, Wi-Fi drop, unplugged cable | No fresh command → every relay opens. |
| 4 | **Firmware duty/burst clamps** | buggy or malicious commands | Enforced even on well-formed packets. |
| 5 | **Operator `X` e-stop** | nothing else | Convenience only: needs PC + app + link all alive. |

**The controller is the performance layer. The firmware is the safety layer.**
Nothing in `controller/` is trusted by `firmware/`.

---

## 2. Hard limits (firmware/config/settings.py)

| Limit | Value | Why |
|---|---|---|
| `DUTY_MAX` | 0.70 | The muscle must rest within every PWM period. |
| `MAX_BURST_MS` | 4000 | Longest continuous stimulation before a forced rest. |
| `COOLDOWN_MS` | 2000 | Enforced rest after a max burst. |
| `COMMAND_TIMEOUT_MS` | 500 | Watchdog: silence ⇒ all relays open. |
| `MIN_PULSE_MS` | 25 | Below this a relay cannot cleanly transition; pulse is dropped. |

### The current ceiling is set by hand, and that is deliberate

The AUVON AS8016 is a **constant-current** source. Its intensity level is set
manually before a run and **capped at a comfortable maximum**. Because the
device regulates current, that hand-set cap *physically bounds peak current* —
software can only ever gate the channel on and off, lowering the **average**.
No software bug can exceed the level you dialled in.

---

## 3. Electrical rules

- **Never route current across the chest.** One limb only. This is why there is
  no adductor channel: the natural adductor (pectoralis major) would mean chest
  electrodes near the heart. Gravity handles adduction instead.
- **Keep the stimulator output floating.** The AS8016 is battery-powered and
  galvanically isolated. Never tie either output leg to system/battery ground —
  that defeats the isolation and creates a path through the subject.
- **Never leave a channel open-circuit while the unit is running.** A current
  source driving an open circuit charges its output capacitor to the compliance
  voltage (100 V+); reconnecting dumps it as a painful jolt. Use the dummy-load
  wiring in [`WIRING.md`](WIRING.md).
- Keep channel A and channel B wiring physically separated; the unit keeps them
  isolated and so should you.
- Never move electrodes with the output live.

---

## 4. Subject screening

Do **not** stimulate anyone with:

- a pacemaker or any implanted electronic/life-support device
- epilepsy or seizure history
- a cardiac condition
- pregnancy
- metal implants in the stimulated limb
- broken, irritated, or infected skin at the electrode site
- neuropathy or any loss of sensation in the limb (they cannot report pain)
- malignancy in the area, DVT, or acute injury/fracture in the limb

Get **informed consent**. The subject must understand that their arm will move
without their control, and must be able to stop it at any moment.

If the event has organisers, **get written clearance** before running this on a
person in public. Some venues prohibit it outright.

---

## 5. Session procedure

**Before connecting a person**

1. Power the board with the TENS units **disconnected**.
2. Confirm every relay is open at boot. Set a multimeter to continuity and
   probe **COM–NO** on each of the 8 relays: all must read **open**, and
   **COM–NC** must read **closed** (the dummy-load path).

   > **This check exists because getting it wrong inverts the fail-safe.**
   > COM–NO is the path to the **subject**. If the idle GPIO level energises
   > the relay, then boot, watchdog expiry, e-stop and every PWM gap all
   > connect the person to a live output while the dummy resistor sits unused.
   > Set `CHANNEL_ACTIVE_LOW` in `firmware/config/pins.py` to match your
   > modules: `False` for HIGH-trigger (ours), `True` for LOW-trigger.
3. Confirm the software comes up disarmed and that the kill latches
   (venv active — see the [README](../README.md#0-set-up-the-virtual-environment-once)):
   ```bash
   cd controller && python run.py --sim
   ```
   The status line must start `[disarmed]`. Press `A` → `[ARMED  ]`,
   press `X` → `[KILLED  ]` and duties show `idle`.
4. Verify the dummy load. Put a scope across a channel's **two output
   terminals** with that channel off: the voltage must sit near I·R (a few
   volts), **not** climb toward 100 V+ compliance.
5. Verify the watchdog. With `python tools/bench.py`, type `arm` then
   `all 0.5`, then kill the process with `Ctrl-C`: every relay must open
   within 500 ms.

Full step-by-step in [`TESTING.md`](TESTING.md).

**Connecting**

6. Units **off**, then place electrodes. Never apply pads with output live.
7. Give the subject the in-line kill switch and have them open and close it
   once, in the posture they will hold, to prove they can reach it.
8. Set mode + intensity **by hand**, starting from the lowest level, ramping
   only to a comfortable, clearly-tolerable contraction. Note the level per
   muscle — changing mode resets intensity to zero.

**Running**

9. Press `A` to arm only when everyone is ready.
10. Keep a hand on `X`. Stop immediately on any report of pain, skin irritation,
    cramping, or dizziness.
11. Watch for fatigue: contractions weakening over a session means the muscle is
    tiring. Rest it.

**After**

12. Press `Q` (disarms on exit), then power down the units **before** removing
    electrodes.
13. Inspect the skin. Redness that does not fade within a few minutes is a stop
    condition — do not run that site again today.

---

## 6. Known limitations — do not design around them

- **Surface FES is not selective.** Stimulating one muscle recruits neighbours.
  Shoulder muscles are deep and coarse; expect imprecise shoulder control.
- **Response varies enormously between people.** Calibrate per subject, every time.
- **Fatigue drifts the plant.** The integrator compensates, which means duty
  creeps up over a session — a reason the burst/cooldown limits exist.
- **The relay PWM carrier is ~6-8 Hz.** Force visibly ripples; limb inertia
  smooths position, not force.
- **This is a demonstrator, not a medical device.** It is not cleared,
  validated, or safe for therapeutic use on patients.
