# Wiring

> Read [`SAFETY.md`](SAFETY.md) first. ESP32 GPIO must never connect directly to
> the TENS unit's output circuits.

## Bill of materials

| Item | Qty | Role |
|---|---|---|
| Axiometa Genesis Mini v1r2 (ESP32-S3-Mini-1-N4R2) | 1 | controller |
| AUVON AS8016 TENS/EMS unit | 2 | stimulator (4 channels each) |
| TONGLING JQC-3FF-S-Z 4-relay module | 1 | channels CH1–CH4 |
| SONGLE SRD-05VDC-SL-C 4-relay module | 1 | channels CH5–CH8 |
| HK4100F-DC3V-SHG relay | 1 | timer keep-alive (both units) |
| 5 V supply | 1 | relay coils |
| ~1 kΩ resistor (rated for stim voltage) | 8 | dummy load per channel |
| Transistor + flyback diode | 1 | driver for the 3 V keep-alive relay |
| Normally-closed pushbutton | 1 | hardware e-stop (recommended) |
| In-line kill switch | 1 | **subject-held, mandatory** |
| Surface electrode pads | 16 | 2 per channel |

## Pin map

The Genesis Mini brings out 12 GPIO on four **AX22 ports**. Those 12 are the
only pins available; we use 10.

| Signal | GPIO | AX22 port | Goes to |
|---|---|---|---|
| CH1 biceps | 4 | P1.IO0 | relay module 1, IN1 |
| CH2 triceps | 5 | P2.IO2 | relay module 1, IN2 |
| CH3 anterior deltoid | 6 | P2.IO1 | relay module 1, IN3 |
| CH4 posterior deltoid | 7 | P2.IO0 | relay module 1, IN4 |
| CH5 middle deltoid | 15 | P3.IO2 | relay module 2, IN1 |
| CH6 spare | 16 | P3.IO1 | relay module 2, IN2 |
| CH7 finger flexors | 17 | P4.IO1 | relay module 2, IN3 |
| CH8 finger extensors | 18 | P4.IO2 | relay module 2, IN4 |
| Timer keep-alive | 2 | P1.IO2 | HK4100F driver |
| **Hardware e-stop** | **9** | P3.IO0 | NC button → GND |
| *(on-board NeoPixel)* | 21 | — | status indicator, nothing to wire |

Spare port pins: **GPIO1** (P4.IO0), and **GPIO3** (P1.IO1) which is a strapping
pin and best left alone. Prefer GPIO1.

### Migrated from the Goouuu N16R8

Channels CH1–CH8 and the timer line are on **the same GPIO numbers as before**,
because the pins we were already using are exactly the ones this board exposes.
The harness moves across essentially unchanged.

**One wire must move: the e-stop, GPIO8 → GPIO9.** GPIO8 is the battery-sense
pin on the Genesis Mini. Left on 8, the e-stop would have fought the battery
monitor — a genuine hardware fault presenting as an intermittent mystery.

### Pins you must not use on the Genesis Mini

The reserved set is *different* from the old board, and mostly for board
reasons rather than silicon ones:

| Pin(s) | Why |
|---|---|
| GPIO8, 34, 46 | **board:** battery sense / status / enable — GPIO8 was our old e-stop |
| GPIO10, 11 | **board:** I2C (STEMMA QT) |
| GPIO12, 13, 14 | **board:** SPI bus |
| GPIO21 | **board:** NeoPixel — used deliberately by `lib/status_led.py` |
| GPIO45 | **board:** user button (also a strapping pin) |
| GPIO26–32 | SPI flash |
| GPIO0, 3, 45, 46 | strapping pins. GPIO3 *is* on port P1, so it is reachable and tempting — a relay module's pull-down on it changes the JTAG source at reset |
| GPIO19, 20 | native USB (USB-C CDC/JTAG) |
| GPIO43, 44 | UART0 console/REPL |

GPIO33–37 were reserved on the old board for its **octal** PSRAM. This board's
PSRAM is **quad**, so they are electrically free — but the Genesis Mini does not
route them to a header, and GPIO34 is the battery monitor, so they remain
unusable in practice.

`config/pins.py` asserts all of this at boot via `assert_no_conflicts()`, which
now also checks the **e-stop input** (it previously checked outputs only, which
is exactly why `ESTOP_PIN = 8` passed silently) and rejects any pin that no AX22
port brings out.

## Relay modules

- Power module `VCC` from the **5 V supply**, not the board's regulator — coils
  draw ~70–90 mA each and will brown out the ESP32.
- Tie module `GND` to ESP32 `GND`. Drive `IN1–IN4` from the GPIOs above.
- If the module has a `VCC` / `JD-VCC` jumper, **split it** and feed `JD-VCC`
  separately so the coil side stays opto-isolated.
- Both modules are **low-level trigger**: GPIO LOW energises. Their onboard
  pull-ups hold every relay **open** while the ESP32 pins float at power-up —
  fail-safe by construction, which is exactly what we want.

## The turn-on jolt, and the dummy load that fixes it

**Symptom.** Closing a channel relay produces a sharp jolt, worse at higher
intensity.

**Cause.** The AS8016 output is a constant-*current* source. With the relay
open it is driving an open circuit, so its voltage ramps to the **compliance
limit** (100 V+), charging the output capacitor. Closing the relay dumps that
stored charge into the skin in one spike. It is capacitive (happens on
*connect*), not an inductive kick.

**Fix.** Never present the unit with an open circuit. Each channel's relay is
SPDT, so use the NC contact to park a dummy load across the output:

```
Device out A ── COM
                 ├─ NO ── active electrode ─( body )─ return electrode ── Device out B
                 └─ NC ── R (~1k) ─────────────────────────────────────── Device out B
```

- Energised → COM–NO: current flows A → body → B. R idle.
- De-energised → COM–NC: **R sits across A–B**, clamping compliance voltage.

**The resistor's free end must go to output terminal B, not back to COM.**
Wiring it COM→NC shorts it out in the off state and does nothing.

Size `R` ≈ body/electrode impedance (~1 kΩ) so the source settles to a similar
operating point in both states, making the hand-off transient-free.

*Bench check:* put a scope across the channel's two output terminals (A–B) with
that channel **off**. Voltage should sit near I·R — a few volts — instead of
climbing toward 100 V+ compliance. Step 2.1 in [`TESTING.md`](TESTING.md).

## Timer keep-alive

The AS8016 auto-offs after 20 minutes. One HK4100F is wired across the **TIMER
button of both units**; firmware pulses it every 5 minutes.

- The coil is **3 V** — do not drive it straight from a 3.3 V GPIO. Use a
  transistor + flyback diode (or a small driver module) off GPIO2.
- **Verify the press behaviour on the bench before trusting it.** The TIMER
  button *adjusts session duration*, so a press may step 20 → 30 → 40 min
  instead of restarting the countdown. Fire a single press with:

  ```bash
  python tools/bench.py
  ```
  ```
  bench> timer
  ```

  Watch both LCDs and confirm three things: the countdown restarts, the
  duration does **not** change, and mode/intensity are untouched. If it cycles
  the duration instead, pick a different keep-alive action and edit
  `_service_timer()` in `firmware/lib/stim_array.py`. Full procedure: step 2.3
  in [`TESTING.md`](TESTING.md).

## E-stop wiring and noise immunity

Normally-closed pushbutton between **GPIO9 and GND**, using the ESP32's internal
pull-up. At rest the closed button holds the pin LOW; pressing (or a broken
lead) lets it float HIGH, which triggers the kill.

**This line is noise-sensitive, and it has caused a real false trip.** The
ESP32's internal pull-up is weak (~45 kΩ), and relay coils switching nearby
couple enough energy into an unshielded lead to lift the pin briefly. The
firmware now requires **~40 ms of continuously open circuit** before killing,
and does **not** use an edge interrupt (a microsecond spike used to be enough).

If false trips still occur, fix it in hardware rather than raising the debounce
further:

1. **Add an external pull-down… no — a stronger pull-up.** Fit a **10 kΩ
   resistor from GPIO9 to 3V3**. This parallels the weak internal pull-up so
   the *closed* button still wins easily, but it does not help noise on its own —
   the real gain is item 2.
2. **Add an RC filter at the pin: 100 nF from GPIO9 to GND.** With the pull-up
   this forms a low-pass filter that swallows coupled spikes before the pin
   sees them. Cheapest and most effective single change.
3. **Route the e-stop lead away from the relay wiring** — do not run it parallel
   to coil leads in the same bundle. Cross at right angles if they must meet.
4. **Use shielded or twisted-pair cable** for the button, with the shield
   grounded at the board end only.
5. Keep the lead **short**. A long unshielded run is an antenna.

Items 2 and 3 together resolve nearly all cases.

## Electrode placement

Two pads per channel, placed **longitudinally along the muscle belly** over the
motor point, a few cm apart. Clean the skin, use conductive gel, and **mark
good positions** once found — placement is the single biggest determinant of
whether this works.

| CH | Muscle | Placement |
|---|---|---|
| 1 | Biceps / brachialis | Anterior upper arm: proximal belly + near elbow crease |
| 2 | Triceps | Posterior upper arm, over long/lateral head |
| 3 | Anterior deltoid | Front of shoulder |
| 4 | Posterior deltoid | Back of shoulder |
| 5 | Middle deltoid | Lateral shoulder, over the mid-deltoid bulge |
| 6 | *spare* | — |
| 7 | Finger flexors (FDS/FDP) | Volar (palm-side) proximal forearm |
| 8 | Finger/wrist extensors | Dorsal (back) proximal forearm |

**No adductor channel.** Gravity lowers the arm. The natural adductor is
pectoralis major — chest electrodes near the heart, which is forbidden.

## Intensity banks

Each AS8016 shares one intensity level across A1/A2 and another across B1/B2 —
so only **2 independent amplitudes per unit, 4 total**. Per-muscle
independence comes from **PWM gating**, not amplitude. Group similarly-sized
muscles on a bank: the small forearm muscles (CH7/CH8) should share a bank at a
lower level than the deltoids.

## Bring-up order

Each step maps to a numbered check in [`TESTING.md`](TESTING.md) — follow the
commands there rather than improvising.

| # | Step | Command / check | `TESTING.md` |
|---|---|---|---|
| 1 | Board only; all relays open at boot | multimeter COM–NO on all 8 = open | 1.3 |
| 2 | Relay modules wired, TENS **disconnected**; each GPIO clicks the right relay | `python tools/bench.py` → `arm`, `pulse 1 0.7 3` … `pulse 8 0.7 3` | 1.5 |
| 3 | Watchdog opens relays on controller death | `arm`, `all 0.7`, then `Ctrl-C` | 1.6 |
| 4 | Dummy-load resistors fitted; off-state clamped | scope A–B, channel off | 2.1 |
| 5 | No jolt on connect | scope A–B, `pulse 1 0.7 2` | 2.2 |
| 6 | Timer keep-alive behaves | `bench> timer`, watch both LCDs | 2.3 |
| 7 | PWM pattern and burst limits on a 1 kΩ load | `pulse 1 0.5 10`, `pulse 1 0.05 5` | 3.1–3.3 |
| 8 | Only now: a consenting, screened subject at the lowest intensity | `python tools/calibrate.py --channel 1 --joint elbow --manual` | 4 |
