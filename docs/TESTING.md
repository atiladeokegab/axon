# Testing & bring-up

Work down this list in order. Do not skip ahead to a person.

Every step below is a command you can actually run. Where a step needs a
physical instrument, the exact probe points and expected reading are given.

Commands assume you are in the repository root unless stated otherwise.

> **Activate the virtual environment first, in every new terminal:**
>
> ```powershell
> .venv\Scripts\Activate.ps1      # Windows PowerShell
> ```
> ```bash
> source .venv/bin/activate       # macOS / Linux
> ```
>
> The prompt must show `(.venv)`. If `mpremote` or `esptool` is "not found",
> this is why.
>
> **Windows: if your system does not recognise `python`, use `py`.** Activating
> the venv normally makes `python` work; if it still does not, substitute `py`
> (or `.venv\Scripts\python.exe`) wherever `python` appears below. First-time
> setup: [README](../README.md#0-set-up-the-virtual-environment-once).

> **Do you need `--host`?** `bench.py`, `calibrate.py`, `deploy_wifi.py` and
> `run.py` all reach the board over Wi-Fi and try to **auto-discover** it first.
> Discovery needs **inbound UDP** through your firewall.
>
> - **Add the firewall rule once** and you can drop `--host` everywhere
>   (Administrator PowerShell):
>   ```powershell
>   New-NetFirewallRule -DisplayName "Juno FES UDP" -Direction Inbound -Protocol UDP -LocalPort 8080,9090 -Action Allow -Profile Any
>   ```
> - **Otherwise pass `--host <board-ip>`** — it always works and skips discovery
>   entirely. The board prints its IP in the boot log.
>
> Commands below show `--host <board-ip>`; omit it once discovery works. (USB
> commands — `mpremote`, `esptool` — are unaffected.)

---

## Stage 0 — Software only (no hardware)

```bash
cd controller
python test_simulation.py
```

Expected final lines:

```
------------------------------------------------------------
74 checks, 0 failed
All checks passed.
```

If anything fails, stop and fix it before touching hardware.

Then drive the virtual arm by hand:

```bash
python run.py --sim
```

A help block prints at startup (press `?` to see it again). The status line
reads `act` = where the arm actually is, `tgt` = where you commanded it.

- Press `A` — the state flag changes `[DISARMED]` → `[ARMED   ]`.
- Press `↑` a few times — the `tgt` numbers for elbow and flex rise, and the
  `act` numbers should follow within a couple of seconds while `stim:` shows
  `CH1:...` and `CH3:...` firing.
- Press `→` a few times — `abd tgt` rises and `CH5` fires. (`←` from zero does
  nothing: abduction is limited to 0–90°, and gravity handles the return.)
- Press `G` — `grip:` flips to `CLOSED` and `CH7` appears in `stim:`. Press `G`
  again to open. **Grip is `G`, not Shift.**
- Press `X` — state becomes `[KILLED  ]` and `stim:` drops to `idle`.
- Press `A` to re-arm, then `Q` to exit.

If `act` never moves and `stim:` stays `idle`, read the hint at the end of the
status line — it names the reason (`press A to arm`, `NO POSE DATA`, etc.).

---

## Stage 1 — Board only (no TENS units connected)

Full setup instructions are in [`DEPLOY.md`](DEPLOY.md). This is the
verification pass.

### 1.1 Flash and copy the firmware (USB, once)

Install the flashing tools into the venv (once):

```powershell
py -m pip install -r requirements.txt
```

Find the board's serial port — **do not assume a number**, it differs per
machine, per USB socket and per board:

```powershell
mpremote connect list
```

The line describing an ESP32 / USB JTAG / CP210x / CH340 device is your board;
use that port wherever `<PORT>` appears below (e.g. `COM7`, `/dev/ttyUSB0`). If
nothing is listed, re-plug the board, then suspect a charge-only USB cable or a
missing USB-serial driver. Details in [`DEPLOY.md`](DEPLOY.md).

Download the firmware — **it is not in this repo**. Take the **`SPIRAM_OCT`**
variant (this board is N16R8 = *octal* PSRAM; the plain build is wrong) from the
"Firmware (Support for Octal-SPIRAM)" section of
<https://micropython.org/download/ESP32_GENERIC_S3/>. `<FIRMWARE.bin>` below
means the full path to that downloaded file, e.g.
`%USERPROFILE%\Downloads\ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin`.

Put the board in download mode: hold **BOOT**, tap **RESET**, release **BOOT**.

```powershell
py -m esptool --chip esp32s3 --port <PORT> erase_flash
py -m esptool --chip esp32s3 --port <PORT> --baud 460800 write_flash -z 0 <FIRMWARE.bin>
```

(The `py -m esptool` module form works whether or not the `esptool` console
script lands on your PATH. On macOS/Linux use `python -m esptool` and a port
like `/dev/ttyUSB0`.)

Tap **RESET** so the board leaves download mode and starts MicroPython, then
confirm the REPL answers before going further:

```powershell
mpremote
```

Expected:

```
Connected to MicroPython at COM11
Use Ctrl-] or Ctrl-x to exit this shell
```

**Then press Enter** — mpremote attaches silently and only draws the `>>>`
prompt once you do. Sanity-check it:

```python
>>> import os; os.listdir()
```

Press `Ctrl-]` to exit. **If it hangs with no output at all, MicroPython is not
running** — do not continue; see "mpremote hangs" in [`DEPLOY.md`](DEPLOY.md).

> **Do not press the physical RESET button while mpremote is attached.** This
> board uses the ESP32-S3's *native* USB, so a hardware reset makes the COM port
> disappear and re-enumerate, and mpremote fails with *"failed to access COM11
> (it may be in use by another program)"*. Use **Ctrl-D** (MicroPython soft
> reset) instead — it re-runs `boot.py`/`main.py` and shows the boot log without
> dropping USB.

Create the two folders on the board's own filesystem, then copy the tree.
(`mpremote`'s `:` prefix means "the board's filesystem", so `:lib` is a folder
named `lib` in the board's root — not a folder on your PC. Note it is `:lib`,
not `:libc`.)

```bash
mpremote fs mkdir :lib
mpremote fs mkdir :config
mpremote fs cp -r firmware/. :
```

Verify the files actually landed:

```bash
mpremote fs ls :
mpremote fs ls :lib
mpremote fs ls :config
```

You should see `boot.py` and `main.py` in the root, six modules in `lib`, and
four in `config` (including `device_secrets.py`).

### 1.2 Check it boots safe and joins Wi-Fi

Turn your hotspot on first (2.4 GHz — see [`DEPLOY.md`](DEPLOY.md)), then:

```bash
mpremote
```

Press **Ctrl-D** (soft reset — **not** the physical RESET button, see the note
above). Expected output:

```
[boot] all channels de-energised (safe state)
[wifi] connecting to 'faisal_network' ...
[wifi] connected: 192.168.137.xxx
[webrepl] started - wireless deploy available
[main] ready - awaiting commands on 192.168.137.xxx:8080
[main] DISARMED at boot; PC must send {"arm":true} to enable stim
```

Write down that IP. Press `Ctrl-]` to exit mpremote.

### 1.3 Multimeter check — the most important physical test

With the relay modules wired but **TENS units disconnected**, set your meter to
continuity/resistance and probe **COM to NO** on each of the 8 relays.

- Expected at boot: **open circuit** on every channel (no continuity COM–NO).
- Expected COM–NC: **closed** (this is the dummy-load path).

If any channel reads closed COM–NO at boot, stop. Check `CHANNEL_ACTIVE_LOW` in
`firmware/config/pins.py` matches your module's trigger polarity.

### 1.4 Go wireless

Unplug USB. Power the board from the 5 V pin (see [`DEPLOY.md`](DEPLOY.md)).

```bash
python tools/deploy_wifi.py                      # auto-discover
python tools/deploy_wifi.py --host <board-ip>     # if discovery is blocked
```

Expected: a list of `OK` lines, one per file. From here you should never need
the USB cable again.

> **Auto-discovery needs inbound UDP through your firewall.** If it reports
> "no board responded", add the firewall rule (see [`DEPLOY.md`](DEPLOY.md)) or
> just pass `--host <board-ip>` — the board prints its IP at boot.

### 1.5 Verify each GPIO drives the right relay

```bash
python tools/bench.py                      # auto-discover
python tools/bench.py --host <board-ip>    # if discovery is blocked
```

At the prompt:

```
bench> arm
bench> pulse 1 0.7 3
```

Channel 1's relay should click and stay closed for 3 seconds. Confirm with your
ear and by probing COM–NO on relay 1. Repeat for channels 2–8:

```
bench> pulse 2 0.7 3
bench> pulse 3 0.7 3
...
bench> pulse 8 0.7 3
```

If the wrong relay fires, fix the GPIO mapping in `firmware/config/pins.py`.

### 1.6 Watchdog test

```
bench> arm
bench> watchdog
```

The tool holds every channel for 3 s, then **stops sending anything at all**.
Every relay must open ~500 ms into that silence. Verify by ear and meter; the
tool also reports `PASS` if the board confirms `watchdog_expired`.

This is the safety backstop for a crashed controller — it must work.

> **Do not test this with `Ctrl-C`.** Two reasons it proves nothing:
> a bare `all 0.7` is a *single packet*, so the relays have already opened on
> their own before you can press anything; and `Ctrl-C` is caught by the tool,
> which then sends an explicit `kill` — so you would be testing the kill path,
> not the watchdog. The `watchdog` command goes silent without sending a kill,
> which is the only way to exercise it honestly.

> **Sustained output must be re-sent.** The board opens all relays after 500 ms
> without a command. `pulse`, `all` and `watchdog` re-send for you; a bare
> `1 0.5` sends one packet and lasts under half a second. If a channel seems to
> "stop working", this is almost always why.

### 1.7 E-stop test

```bash
python tools/bench.py --host <board-ip>
```

```
bench> arm
bench> pulse 1 0.7 10
```

While it is running, press `Ctrl-C`. The tool sends a latching kill. Reconnect
and confirm channels stay dead until you type `arm` again.

### 1.7b Hardware-in-the-loop: full chain, no pose estimator

`bench.py` fires channels one at a time. This runs the **entire control stack**
against real relays — PI controllers, joint→muscle mapping, UDP, firmware PWM —
driven by the simulated arm, so no pose service is needed.

```bash
cd controller
python run.py --sim-hw --host <board-ip>
```

The status flag reads **`SIM+HW`**. Press `A`, then jog with the arrow keys.

What to verify:

- **`↑`** → CH1 (biceps) and CH3 (anterior deltoid) relays click; CH2/CH4 stay silent.
- **`↓`** → CH2 and CH4 click instead — the *antagonists*, never both of a pair together.
- **`→`** → CH5 (middle deltoid) clicks. `←` falls silent (gravity does adduction).
- **`G`** → CH7 clicks and holds; press again and it releases.
- **`X`** → everything stops instantly and stays stopped until `A`.
- Watch `stim:` in the status line — the duties shown should match the relays
  you hear.

This is the last check that exercises the real signal path end to end, and it
runs with **nothing connected to a person**.

> **Relays fire in this mode.** Keep the TENS units disconnected, or at minimum
> keep every electrode off skin.

### 1.8 Link-loss test

```
bench> arm
bench> all 0.5
```

Turn the **hotspot off**. Every relay must open. Turn it back on: the board
reconnects (see WebREPL log) but stays **disarmed** until you `arm` again.

---

## Stage 2 — Dummy load and jolt check (TENS units connected, no person)

Connect the TENS units and the dummy-load resistors as in
[`WIRING.md`](WIRING.md). **No electrodes on anybody.**

### 2.1 Verify the dummy load clamps the output

Put a scope across a channel's **two output terminals** (A and B) with the
channel **off** (relay de-energised, resting on NC).

- Expected: voltage clamped near `I × R` — a few volts at most.
- Bad: voltage ramping upward toward 100 V+ (compliance).

If it ramps, the dummy resistor is mis-wired. The free end must go to output
terminal **B**, not back to COM. See [`WIRING.md`](WIRING.md).

### 2.2 Verify there is no jolt on connect

With the scope still attached:

```bash
python tools/bench.py --host <board-ip>
```

```
bench> arm
bench> pulse 1 0.7 2
```

Watch the moment the relay closes. Expected: stimulation starts at its normal
amplitude. Bad: a large spike on the first pulse — that is the capacitive dump,
meaning the dummy load is not doing its job.

### 2.3 Verify the timer keep-alive

This is the check most likely to surprise you: the AS8016's TIMER button
*adjusts the session duration*, so a press may not do what we assume.

```bash
python tools/bench.py --host <board-ip>
```

```
bench> timer
```

Watch both units' LCDs. You need to confirm:

- the auto-off countdown **restarts**, and
- the timer **duration** does not change (e.g. it does not step 20 → 30 → 40),
- and mode / intensity are untouched.

If the press cycles the duration instead of resetting the countdown, find a
different keep-alive action (for example a brief press on a harmless button)
and change `_service_timer()` in `firmware/lib/stim_array.py`.

Then leave the system idle and confirm the units are still on after 25 minutes:

```bash
python tools/bench.py --host <board-ip>
```

```
bench> status
```

---

## Stage 3 — Bench load (still no person)

Replace the body with a resistive load (~1 kΩ) across each channel's electrode
leads so you can measure without stimulating anyone.

### 3.1 Duty produces the expected on/off pattern

```
bench> arm
bench> pulse 1 0.5 10
```

Scope across the load. At `PWM_PERIOD_MS = 150` and duty 0.5, expect bursts of
roughly **75 ms on / 75 ms off** (~6.7 Hz).

### 3.2 Sub-minimum pulses are dropped, not half-actuated

```
bench> pulse 1 0.05 5
```

0.05 × 150 ms = 7.5 ms, which is below `MIN_PULSE_MS = 25`. Expected: the relay
stays **completely silent**. Bad: buzzing/chattering.

### 3.3 Burst limit and cooldown

```
bench> pulse 1 0.7 10
```

Expected: stimulation runs for `MAX_BURST_MS` (4 s), then cuts out for
`COOLDOWN_MS` (2 s), then resumes. You should hear the gap.

---

## Stage 4 — First human trial

**Read [`SAFETY.md`](SAFETY.md) in full first.** Screened, consenting subject
only, with the in-line kill switch in their hand.

### 4.1 Place electrodes and set intensity by hand

1. TENS units **off**. Place electrodes per the table in [`WIRING.md`](WIRING.md).
2. Power the units on. Select the EMS mode, then raise intensity **from the
   lowest level**, one step at a time, to a clear but comfortable contraction.
3. Write down the level per muscle. Remember: changing mode resets intensity.

### 4.2 Calibration sweep — per channel, per subject

```bash
python tools/calibrate.py --channel 1 --joint elbow --manual --host <board-ip>
```

It ramps duty in 7 steps, holding 3 s each with 3 s rest, and asks you for the
observed angle at each step. Output goes to `calibration_ch1.json` and prints
the activation threshold and total range achieved.

Repeat for the muscles you plan to use:

```bash
python tools/calibrate.py --channel 3 --joint shoulder_flex --manual --host <board-ip>
python tools/calibrate.py --channel 5 --joint shoulder_abd --manual --host <board-ip>
python tools/calibrate.py --channel 7 --joint grip --manual --host <board-ip>
```

If a channel shows "no movement detected", that muscle has no authority on this
subject — move the electrodes, raise the hand-set intensity, or drop that DOF
from the demo.

### 4.3 Single-joint closed loop

Start with the elbow only:

```bash
cd controller
python run.py --host <board-ip>     # omit --host once discovery works
```

Press `A`, then `↑`/`↓`. Expected: the measured elbow angle converges on the
target within **±5–10°** in **2–4 seconds** and then holds without buzzing.

If it never arrives, see the troubleshooting table below. If it buzzes at the
target, widen the deadband in `controller/settings.py`.

### 4.4 Add the remaining DOF

Add shoulder (`←`/`→`), then grip (`G`). Shoulder will be visibly coarser than
elbow — that is expected with surface FES.

---

## Stage 5 — Demo rehearsal

- Run the full sequence end to end several times, with rest between runs.
- **Mark electrode positions** (skin-safe marker) so setup is reproducible.
- Pre-hook the subject before going on stage; it takes minutes.
- Rehearse the failure narration: if a pose misses, say so and let the loop
  correct. An honest correction reads as control; a silent miss reads as broken.
- Charge everything: both TENS units, the 5 V supply/battery, the laptop.
- Bring spares: electrodes, one relay module, cables.
- Confirm the subject can reach their kill switch in the demo posture.
- Confirm the hotspot is on and the laptop is joined to it, not to venue Wi-Fi.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pose:STALE` in the status line | Estimator not sending, wrong port, or slower than ~3 Hz. See [`POSE_API.md`](POSE_API.md). |
| Arm drives the wrong way | Coordinate frame mismatch in the pose feed, or agonist/antagonist electrodes swapped between the pair. |
| Relays chatter at the target | Deadband too narrow or `Kp` too high in `controller/settings.py`. Do **not** add a D term. |
| Never reaches the target | Hand-set intensity too low; electrodes off the motor point; or `i_limit` too small (see [`CONTROL.md`](CONTROL.md)). Re-run `tools/calibrate.py`. |
| Jolt when a channel switches on | Dummy load missing or wired COM→NC instead of NC→terminal B. Re-do check 2.1. |
| Contraction fades during a session | Muscle fatigue. Rest it, or move the electrodes. Expected behaviour. |
| Units switch off after ~20 min | Timer keep-alive not resetting the countdown. Re-do check 2.3. |
| Board resets when relays fire | 5 V supply too small, or coils drawing through the board. Use ≥2 A and power the coil side separately. |
| `[link] no board responded` | Board unpowered, not on the hotspot, or hotspot client isolation is on. Try `--host <ip>` directly. |
| Nothing happens after `arm` | Watchdog: the tool must keep sending. Use `pulse`, not a bare duty command, to hold a channel. |
| `mpremote` / `esptool` not found | The venv is not active in this terminal. Re-run the activate command, or use `py -m esptool ...`. |
| `python` is not recognised (Windows) | Use `py` instead, or activate the venv first. Last resort: `.venv\Scripts\python.exe`. |
| `ModuleNotFoundError` | Wrong interpreter. Check `python -c "import sys; print(sys.prefix)"` points inside `.venv`. |
