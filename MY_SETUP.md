# My setup — copy/paste commands

Personalised for **this** machine and board. The general docs in `docs/` explain
*why*; this file is just the commands.

| | |
|---|---|
| Board | Goouuu Tech **ESP32-S3-N16R8** (16 MB flash, **octal** PSRAM) |
| Project folder | `C:\Users\faisa\Desktop\juno_hack` |
| venv | `.venv` in the project root |
| Python launcher | **`py`** (this machine does not recognise `python` outside the venv) |
| Firmware image | `bin\ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin` |
| Bootloader port | **COM11** (while BOOT is held — for `esptool`) |
| MicroPython port | **COM12** (normal running — for `mpremote`) |
| Network | **Windows Mobile Hotspot on this laptop** |
| Laptop hotspot IP | `192.168.137.1` |
| Board IP | **`192.168.137.154`** (DHCP — re-check if it stops responding) |
| Wi-Fi SSID | `faisal_network` — must match Settings → Mobile hotspot → Network name |
| WebREPL password | `juno2026` |

> **The COM port changes by mode.** Bootloader (BOOT held) and MicroPython
> enumerate as *different* USB devices. `mpremote` auto-detects, so its commands
> omit the port; `esptool` cannot, so it needs `--port COM11`.

---

## Wiring — what connects to what

### ESP32-S3 GPIO → relay module inputs

Two 4-relay modules. **Module 1 drives TENS unit 1, Module 2 drives TENS unit 2.**

| Module | Input | ESP32 GPIO | Channel | Muscle | Electrodes go on |
|---|---|---|---|---|---|
| **1** (JQC-3FF-S-Z) | IN1 | **GPIO4** | CH1 | Biceps / brachialis | Anterior upper arm |
| **1** | IN2 | **GPIO5** | CH2 | Triceps | Posterior upper arm |
| **1** | IN3 | **GPIO6** | CH3 | Anterior deltoid | Front of shoulder |
| **1** | IN4 | **GPIO7** | CH4 | Posterior deltoid | Back of shoulder |
| **2** (SRD-05VDC-SL-C) | IN1 | **GPIO15** | CH5 | Middle deltoid | Lateral shoulder |
| **2** | IN2 | **GPIO16** | CH6 | *spare — leave unwired* | — |
| **2** | IN3 | **GPIO17** | CH7 | Finger flexors (grip close) | Volar (palm-side) forearm |
| **2** | IN4 | **GPIO18** | CH8 | Finger extensors (grip open) | Dorsal (back) forearm |

Plus two single lines:

| Signal | ESP32 GPIO | Connect to |
|---|---|---|
| TIMER keep-alive | **GPIO2** | HK4100F relay; its contacts wire across the **TIMER button of BOTH AUVON units**. Firmware pulses this pin for 250 ms every 5 min to simulate a button press. |
| Hardware e-stop | **GPIO8** | Normally-**closed** pushbutton to **GND** (opens = kill) |

**E-stop behaviour (as wired):** internal pull-up, so at rest the closed button
holds the pin LOW; pressing opens the circuit and the pull-up takes it HIGH,
which triggers the kill. A **cut or unplugged lead also reads HIGH**, so a
broken e-stop trips the kill instead of silently doing nothing. The spring
return is fine — the kill **latches in software** and needs an explicit re-arm.
Firmware checks the level at boot and polls it every loop, so a button already
pressed at power-on (or a lead that falls off mid-run) is caught too.

> **Driving the HK4100F coil straight from GPIO2:** an HK4100F-DC3V coil is
> roughly 45–50 Ω, i.e. **~60–70 mA**. The ESP32-S3 is rated ~20 mA per pin
> (40 mA absolute max), so a bare coil is over the limit even though it appears
> to work, and an unclamped coil dumps inductive kickback into the pin when it
> releases. If GPIO2 feeds a **relay module** with its own transistor and
> flyback diode, this is fine. If it is a **bare relay coil**, add a small
> transistor/MOSFET plus a flyback diode across the coil — it is a few parts,
> and the failure mode is a dead GPIO mid-demo.

### Power

- Relay module `VCC` → **5 V supply** (not the ESP32's 3.3 V — coils need ~70–90 mA each).
- Relay module `GND` → **common ground** with the ESP32.
- If a module has a `VCC` / `JD-VCC` jumper, **split it** and feed `JD-VCC` from
  the 5 V supply so the coil side stays opto-isolated.
- Supply must handle **≥ 2 A** (8 coils + Wi-Fi peaks). Undersized supplies cause
  brownouts that look exactly like random firmware crashes.

### Each relay's output side (per channel)

The relay switches **one leg** of one TENS channel, with a resistor on NC so the
stimulator output is never left open-circuit (that is what causes the turn-on
jolt):

```
TENS out A ── COM
                ├─ NO ── active electrode ─( body )─ return electrode ── TENS out B
                └─ NC ── ~1k resistor ─────────────────────────────────── TENS out B
```

**The resistor's free end goes to output terminal B — not back to COM.** Wiring
it COM→NC shorts it out and does nothing. Full explanation:
[`docs/WIRING.md`](docs/WIRING.md).

Keep the TENS output **floating** — never tie either leg to system ground.

---

## 0. Every new terminal

```powershell
cd C:\Users\faisa\Desktop\juno_hack
.venv\Scripts\Activate.ps1
```

Prompt must show `(.venv)`. If PowerShell refuses:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

---

## 1. First-time setup (already done — for reference / re-imaging)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Flash MicroPython — hold **BOOT**, tap **RESET**, release **BOOT**, then:

```powershell
py -m esptool --chip esp32s3 --port COM11 erase_flash
py -m esptool --chip esp32s3 --port COM11 --baud 460800 write_flash -z 0 bin\ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin
```

Tap **RESET**, then create the folders (WebREPL cannot make directories):

```powershell
mpremote fs mkdir :lib
mpremote fs mkdir :config
```

One-time firewall rule so wireless discovery works — **Administrator** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Juno FES UDP" -Direction Inbound -Protocol UDP -LocalPort 8080,9090 -Action Allow -Profile Any
```

---

## 2. Push code to the board

**Over USB (always works):**

```powershell
mpremote fs cp -r firmware/. :
```

**Over Wi-Fi (no cable):**

```powershell
python tools/deploy_wifi.py --host 192.168.137.154
```

Single file, when iterating:

```powershell
mpremote fs cp firmware/main.py :main.py
mpremote fs cp firmware/config/settings.py :config/settings.py
```

---

## 3. Watch the board boot

```powershell
mpremote soft-reset      # reliable: works even while main.py runs
mpremote                 # then attach to watch the log
```

Expected:

```
[boot] all channels de-energised (safe state)
[wifi] connected: 192.168.137.154
[webrepl] started - wireless deploy available
[main] ready - awaiting commands on 192.168.137.154:8080
[main] DISARMED at boot; PC must send {"arm":true} to enable stim
```

- **Ctrl-C** — break into the running `main.py` to get a `>>>` prompt.
- **Ctrl-]** — exit mpremote.
- **Never press the physical RESET while mpremote is attached** — the USB port
  disappears and you get *"failed to access COM12"*. Use Ctrl-D.

Check the files on the board:

```powershell
mpremote fs ls :
mpremote fs ls :lib
mpremote fs ls :config
```

---

## 4. Bench-test the hardware

```powershell
python tools/bench.py --host 192.168.137.154
```

```
bench> arm
bench> pulse 1 0.7 3
bench> timer
bench> off
bench> q
```

Channels: 1 biceps · 2 triceps · 3 ant.deltoid · 4 post.deltoid ·
5 mid.deltoid · 6 spare · 7 grip close · 8 grip release.

---

## 5. Run the demo

```powershell
cd controller
python run.py --sim                                  # virtual arm, no hardware
python run.py --sim-hw --host 192.168.137.154        # virtual arm, REAL relays
python run.py --host 192.168.137.154                 # full system (needs pose feed)
```

**`--sim-hw` is hardware-in-the-loop** — the virtual arm closes the control
loop, but its duties go to the real board so relays and PWM actually switch.
No pose estimator required, so it is the way to shake out the whole chain
(PI → mapping → UDP → firmware PWM → relay contacts) before anyone is wired up.
The status line shows **`SIM+HW`** so you cannot mistake it for a dry run.

> **Relays fire in this mode. Nothing may be connected to a person.**

Keys: **A** arm · **X** e-stop · **↑↓** raise/lower · **→←** out/in ·
**G** grip toggle · **?** help · **Q** quit.

---

## 6. Offline checks (no hardware)

```powershell
cd controller
python test_simulation.py
```

Expect `74 checks, 0 failed`.

---

## Troubleshooting, this machine

| Symptom | Fix |
|---|---|
| `python` not recognised | Activate the venv, or use `py`. |
| `mpremote`/`esptool` not found | venv not active — re-run the activate line. |
| `mpremote` hangs, no output | MicroPython not running. Re-flash (section 1). |
| Connected but Enter/Ctrl-D do nothing | `main.py` is looping — press **Ctrl-C**. |
| `failed to access COM12` | You pressed the physical RESET while attached. Ctrl-], wait 5 s, reconnect. |
| esptool "could not open COM11" | Board is not in download mode — hold BOOT, tap RESET, release BOOT. |
| `[deploy] board not found` | Firewall (rule in section 1), or the IP changed. Check **Settings → Mobile hotspot → connected devices**, then use `--host <new-ip>`. |
| Every deploy file FAILs | Run with `-v` for the real error; USB fallback: `mpremote fs cp -r firmware/. :` |
| `run.py` connects and arms, but no relay ever fires (bench.py works) | Fixed. The board rejected packets whose sequence number was lower than the last seen, and every tool restarts its counter at 1 — so after a bench session, `run.py` was ignored. Re-deploy `lib/net_udp.py`. |
| A channel "does nothing" | An active channel **buzzes at ~6.7 Hz**, it does not click or sit closed — `DUTY_MAX` is 0.70, so it is 105 ms on / 45 ms off. Use `bench> click 1 5` for an audible 0.5 s on / 0.5 s off pattern. |
| `board:KILLED(hardware_estop)` but nobody pressed the button | Relay-coil noise coupling into the GPIO8 lead. Fixed in firmware `2026-07-25.6-estop-noise` (edge interrupt removed, 40 ms debounce). If it recurs, fix it in hardware: **100 nF from GPIO8 to GND**, and route the e-stop lead away from the coil wiring. See [`docs/WIRING.md`](docs/WIRING.md). |
| `Operations on 2 remote files are not supported` | Fixed. `webrepl_cli.py` treats any argument containing `:` as remote, so a Windows absolute path (`C:\...`) confused it; `deploy_wifi.py` now passes relative paths. Update your copy if you still see this. |
| Board never joins Wi-Fi | Hotspot must be **2.4 GHz** (Properties → Band) and its **Network name must equal `faisal_network`**, or edit `WIFI_SSID` in `firmware/config/device_secrets.py` and re-push. |

**If the board's IP changes:** Settings → Network & internet → Mobile hotspot →
connected devices (its MAC is `CC:BA:97:05:11:A8`). Update the IP in this file.
