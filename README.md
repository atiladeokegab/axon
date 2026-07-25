# Closed-Loop FES Arm Controller

Teleoperate a human arm with surface functional electrical stimulation (FES),
under closed-loop control from an external pose estimate.

An operator presses arrow keys; a PI controller compares the commanded joint
angles against the arm's measured pose and gates eight muscle-stimulation
channels until the real limb matches. Grip is a triggered power grasp.

> **This drives current through a person.** Read [`docs/SAFETY.md`](docs/SAFETY.md)
> before connecting anything to anybody. The safety limits are enforced on the
> microcontroller, deliberately independent of the control software.

---

## Architecture

```
   keyboard (arrows / shift / X)
            |
            v
   +--------------------------+          +---------------------------+
   |   PC  (controller/)      |          |  teammate's pose service  |
   |                          |          |  (vision - not ours)      |
   |  target joint angles     |          +---------------------------+
   |  PI control + mapping    |<---- 3D joints / angles, UDP :9090 --+
   |                          |
   +-----------|--------------+
               |  duty[8] + grip, UDP :8080
               v
   +--------------------------+
   |  ESP32-S3 (firmware/)    |
   |  software PWM -> relays  |     <-- INDEPENDENT SAFETY LAYER
   |  watchdog / duty clamp   |
   +-----------|--------------+
               |
        8 relay channels
               v
     2x AUVON AS8016 TENS/EMS  ->  electrodes  ->  muscles  ->  arm moves
                                                                   |
                                          (observed by the pose service)
```

**Role split.** The PC decides *what the arm should do*. The ESP32 decides
*what is physically allowed*. The board never trusts the PC: if commands stop,
are malformed, or ask for too much, it clamps them or opens every relay.

---

## Repository layout

| Path | What it is |
|---|---|
| `firmware/` | MicroPython for the ESP32-S3 (actuation + safety) |
| `firmware/config/pins.py` | GPIO map, channel→muscle table, reserved-pin traps |
| `firmware/config/settings.py` | PWM timing + **safety envelope** |
| `firmware/lib/stim_channel.py` | One PWM-gated relay channel |
| `firmware/lib/stim_array.py` | The 8 channels + auto-off keep-alive |
| `firmware/lib/safety.py` | Arm/disarm, watchdog, duty clamping |
| `firmware/lib/wifi_manager.py` | Station-mode Wi-Fi + WebREPL |
| `tools/deploy_wifi.py` | Wireless firmware deploy (no USB) |
| `tools/bench.py` | Interactive channel tester for hardware bring-up |
| `tools/calibrate.py` | Per-subject recruitment-curve sweep |
| `controller/` | PC-side control application |
| `controller/run.py` | Teleoperation entrypoint |
| `controller/control_loop.py` | The closed loop |
| `controller/pid.py` | PI controller (see docs for why not PID) |
| `controller/mapping.py` | Joint effort → 8 muscle channels |
| `controller/pose_api.py` | **Pose ingest API** (contract with teammate) |
| `controller/test_simulation.py` | Offline checks, no hardware needed |
| `docs/` | Wiring, API contract, control design, safety, testing |

---

## Quick start

### 0. Set up the virtual environment (once)

All Python commands in this project run inside a venv. From the repository root:

**Windows (PowerShell)** — use `py`, the Python launcher:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

**Windows (cmd.exe):**

```cmd
py -m venv .venv
.venv\Scripts\activate.bat
py -m pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Your prompt should now start with `(.venv)`. **Re-activate it in every new
terminal** — the activate line only, not the `py -m venv` step.

#### `py` vs `python` on Windows

If your machine does not recognise `python`, use **`py`** (the Windows Python
launcher, installed with Python from python.org). You only strictly need it for
the `py -m venv` step above: **activating the venv puts `.venv\Scripts` on your
PATH, so `python` works inside an activated venv even when it fails outside.**

Check which you have:

```powershell
py --version           # should print a version
python --version       # may fail on your system - that's fine
```

Rule of thumb for every command in these docs:

| Situation | Use |
|---|---|
| Creating the venv (before activation) | `py -m venv .venv` |
| Inside an activated venv | `python ...` (works), or `py ...` if you prefer |
| Venv active but `python` still not found | `.venv\Scripts\python.exe ...` |

Verify after activating:

```powershell
python -c "import sys; print(sys.prefix)"     # should point inside .venv
mpremote --version
py -m esptool version
```

If `python` errors here, the venv is not actually active — re-run the activate
line and confirm the prompt shows `(.venv)`.

> If PowerShell blocks activation with an execution-policy error:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### 1. Dry run (no hardware, no estimator)

```bash
cd controller
python test_simulation.py     # 74 offline checks
python run.py --sim           # virtual arm, drive it with the arrow keys
```

### 2. Flash the board (USB, once only)

With the venv active, from the repository root:

Find your board's port first — it differs per machine and per USB socket:

```bash
mpremote connect list        # the ESP32 / USB-JTAG / CP210x line is your board
```

Then, with the octal-SPIRAM MicroPython build for the N16R8 flashed:

```bash
mpremote fs mkdir :lib
mpremote fs mkdir :config
mpremote fs cp -r firmware/. :
```

The board then joins your hotspot and starts WebREPL. Unplug USB and power it
from the **5 V pin** — every later update is wireless:

```bash
python tools/deploy_wifi.py                    # auto-discover
python tools/deploy_wifi.py --host <board-ip>   # if discovery is blocked
```

(Auto-discovery needs inbound UDP through your firewall; `--host` always works.
The board prints its IP at boot.)

See [`docs/DEPLOY.md`](docs/DEPLOY.md).

### 3. Bench-test the hardware

Fire individual channels before any closed-loop run:

```bash
python tools/bench.py                    # auto-discover
python tools/bench.py --host <board-ip>  # if discovery is blocked
```

```
bench> arm
bench> pulse 1 0.7 3      # channel 1 at 70% duty for 3 seconds
bench> timer              # fire the auto-off keep-alive once
bench> off
```

### 4. Run for real

```bash
cd controller
python run.py                        # auto-discover the board
python run.py --host <board-ip>       # if discovery is blocked
```

### Controls

| Key | Action |
|---|---|
| `A` | **arm** — stimulation enabled (nothing moves until you press this) |
| `→` | hand **out** to the side (abduction, range 0–90°) |
| `←` | hand back **in** toward the body (only does anything if abd > 0°) |
| `↑` / `↓` | raise / lower hand (shoulder flex + elbow) |
| `G` | **toggle grip** open/closed — it is `G`, **not Shift** |
| `D` | disarm |
| `X` | **EMERGENCY STOP** (latched; `A` to re-arm) |
| `?` | show the key list again |
| `Q` | quit (disarms on exit) |

Grip is `G` because a terminal cannot detect a bare Shift press or a key
release, so "hold to grip" is not implementable here. `G` toggles instead.

Boot state is **disarmed**. Nothing stimulates until you press `A`.

### Reading the status line

```
[ARMED   ] pose:SIM | elbow act  33.4 tgt  45.0 | flex act 31.3 tgt 45.0 | ... | stim: CH1:0.27 CH3:0.25
```

- **`act`** — where the arm *actually* is, from the pose estimator.
- **`tgt`** — where you have *commanded* it to go (arrow keys move this).
- **`stim`** — which channels are firing, and at what duty. `idle` = nothing on.

The controller stimulates until `act` catches up with `tgt`.

**If nothing moves,** the state flag tells you why, and the line spells it out:

| Shows | Meaning |
|---|---|
| `[DISARMED] ... <- press A to arm` | Targets move, but stimulation is off. |
| `[KILLED  ] ... <- press A to re-arm` | E-stop is latched. |
| `pose:STALE ... <- NO POSE DATA` | No pose estimator running. Use `--sim` for a virtual arm. |

Two more reasons a key can look dead — both are announced on screen:

- **At a joint limit.** `←` at abduction 0° cannot go lower, so `tgt` does not
  move. You will see *"shoulder_abd is already at its limit"*.
- **Inside the deadband.** Each arrow press is 3°, but the controller ignores
  errors under the **3° deadband** — that is what stops the arm buzzing at the
  setpoint. **Press an arrow twice before expecting movement.**

---

## Pin map — GPIO → relay → muscle

| CH | ESP32 GPIO | Relay module / input | Muscle | Joint · role |
|----|-----------|----------------------|--------|--------------|
| 1 | **GPIO4** | Module 1 · IN1 | Biceps / brachialis | Elbow flex |
| 2 | **GPIO5** | Module 1 · IN2 | Triceps | Elbow extend |
| 3 | **GPIO6** | Module 1 · IN3 | Anterior deltoid | Shoulder flex |
| 4 | **GPIO7** | Module 1 · IN4 | Posterior deltoid | Shoulder extend |
| 5 | **GPIO15** | Module 2 · IN1 | Middle deltoid | Shoulder abduct (**gravity adducts**) |
| 6 | **GPIO16** | Module 2 · IN2 | *spare — unused* | — |
| 7 | **GPIO17** | Module 2 · IN3 | Finger flexors | Grip close |
| 8 | **GPIO18** | Module 2 · IN4 | Finger extensors | Grip release |

Module 1 drives TENS unit 1, Module 2 drives TENS unit 2. Two extra lines:
**GPIO2** → TIMER keep-alive relay (across both units' TIMER buttons), and
**GPIO8** → optional hardware e-stop button (normally-closed to GND).

Full wiring — power, the dummy-load resistor that prevents the turn-on jolt,
and electrode placement — is in [`docs/WIRING.md`](docs/WIRING.md).
Source of truth for pins: `firmware/config/pins.py`.

Antagonist pairs are **never co-contracted**. See [`docs/WIRING.md`](docs/WIRING.md)
for electrode placement.

---

## Documentation

- [`MY_SETUP.md`](MY_SETUP.md) — **copy/paste commands for this exact machine,
  board and network** (COM ports, board IP, firmware image). Start here day-to-day.
- [`docs/SAFETY.md`](docs/SAFETY.md) — **read first**; safety layers and procedure
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — Wi-Fi setup, wireless deploy, 5 V power
- [`docs/WIRING.md`](docs/WIRING.md) — relays, dummy load, jolt fix, electrodes
- [`docs/POSE_API.md`](docs/POSE_API.md) — the contract for the pose estimator
- [`docs/CONTROL.md`](docs/CONTROL.md) — why PI, tuning, loop timing
- [`docs/TESTING.md`](docs/TESTING.md) — bring-up order, bench checks

## Scope

No intent detection, no EMG, no BCI. Targets come from the keyboard (demo) or
a scripted exercise trajectory (future clinical app). Vision/pose estimation is
a teammate's service — we only consume it. Grip is a gross power grasp, not
individuated fingers.
