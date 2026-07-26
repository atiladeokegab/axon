# My setup — copy/paste commands

Personalised for **this** machine and board. The general docs in `docs/` explain
*why*; this file is just the commands.

| | |
|---|---|
| Board | Axiometa **Genesis Mini v1 rev 2** — ESP32-S3-Mini-1-**N4R2** (4 MB flash, 2 MB **quad/QSPI** PSRAM) |
| Project folder | `C:\Users\faisa\Desktop\juno_hack` |
| venv | `.venv` in the project root |
| Python launcher | **`py`** (this machine does not recognise `python` outside the venv) |
| Firmware image | `bin\ESP32_GENERIC_S3-20260406-v1.28.0.bin` — **standard, NOT `SPIRAM_OCT`** |
| Bootloader port | **COM8** (while BOOT is held — for `esptool`) |
| MicroPython port | **COM7** (normal running — `mpremote` auto-detects, so its commands omit it) |
| esptool | **v5+** — commands are hyphenated (`erase-flash`, not `erase_flash`) |
| Network | **Windows Mobile Hotspot on this laptop** |
| Laptop hotspot IP | `192.168.137.1` |
| Board IP | **`192.168.137.131`** (DHCP — re-check from the boot log if it stops responding) |
| Wi-Fi SSID | `faisal_network` — must match Settings → Mobile hotspot → Network name |
| WebREPL password | `juno2026` |

> **The COM port changes by mode.** Bootloader (BOOT held) and MicroPython
> enumerate as *different* USB devices. `mpremote` auto-detects, so its commands
> omit the port; `esptool` cannot, so it needs `--port <PORT>`.

> ### Migrated from the Goouuu ESP32-S3-N16R8 — what changed
>
> | | Old (Goouuu N16R8) | New (Genesis Mini N4R2) |
> |---|---|---|
> | PSRAM | 8 MB **octal** | 2 MB **quad (QSPI)** |
> | Firmware image | `...-SPIRAM_OCT-...bin` | **standard** `ESP32_GENERIC_S3-...bin` |
> | CH1–CH8 | GPIO 4,5,6,7,15,16,17,18 | **identical** — no rewiring |
> | TIMER keep-alive | GPIO2 | **identical** |
> | **Hardware e-stop** | GPIO8 | **GPIO9** ← *the only wire you must move* |
> | Status LED | none | on-board NeoPixel, GPIO21 |
>
> Nine of the ten signal wires land on the same pin numbers, because the pins
> we were already using are exactly the ones the Genesis Mini brings out on its
> AX22 ports. **GPIO8 is the battery-sense pin on this board**, so the e-stop
> had to move; leaving it there would have been a real fault presenting as a
> mystery. `firmware/config/pins.py` now refuses to boot on a pin map that
> collides with an on-board function or lands on a GPIO no port exposes.

> ### Before you power the new board — the overheating post-mortem
>
> The relay supply was already separate with only grounds joined, which rules
> out the most common cause. Work through the rest **before** applying power,
> because whatever cooked the first board is still on the bench:
>
> | Check | Why |
> |---|---|
> | **Is GPIO2 driving a bare HK4100F coil?** | ~60–70 mA against a ~20 mA pin rating, re-firing every 5 minutes. This is the prime suspect. Set `TIMER_KEEPALIVE_ENABLED = False` until a transistor + flyback diode is in place. |
> | Meter each relay-module IN pin to GND with the board **unplugged** | A module input with a pull-up to its own 5 V rail pushes current into the ESP32 pin and out through the protection diode into 3.3 V. Silent, and it heats the regulator. |
> | Was USB **and** external 5 V connected at once? | The Genesis Mini has battery-charging circuitry on that rail. Feeding it externally while USB is attached can make the two sources fight. Pick one. |
> | Confirm both relay modules' grounds meet the board ground at **one** point | Two ground paths make a loop, and the return current finds the USB shield. |
> | Check for a shorted or mis-seated jumper on the relay boards (VCC / JD-VCC) | If that jumper is in place, the coils are fed from the logic rail regardless of your separate supply. |
>
> Bring the new board up **with nothing connected to the AX22 ports at all**,
> confirm it boots and joins Wi-Fi, then add one relay module, then the second.

---

## Wiring — what connects to what

### ESP32-S3 GPIO → relay module inputs

Two 4-relay modules. **Module 1 drives TENS unit 1, Module 2 drives TENS unit 2.**

GPIO numbers are unchanged from the old board; the **AX22 port** column is what
tells you which physical connector each one is on now.

| Module | Input | GPIO | AX22 port | Channel | Muscle | Electrodes go on |
|---|---|---|---|---|---|---|
| **1** (JQC-3FF-S-Z) | IN1 | **GPIO4** | P1.IO0 | CH1 | Biceps / brachialis | Anterior upper arm |
| **1** | IN2 | **GPIO5** | P2.IO2 | CH2 | Triceps | Posterior upper arm |
| **1** | IN3 | **GPIO6** | P2.IO1 | CH3 | Anterior deltoid | Front of shoulder |
| **1** | IN4 | **GPIO7** | P2.IO0 | CH4 | Posterior deltoid | Back of shoulder |
| **2** (SRD-05VDC-SL-C) | IN1 | **GPIO15** | P3.IO2 | CH5 | Middle deltoid | Lateral shoulder |
| **2** | IN2 | **GPIO16** | P3.IO1 | CH6 | *spare — leave unwired* | — |
| **2** | IN3 | **GPIO17** | P4.IO1 | CH7 | Finger flexors (grip close) | Volar (palm-side) forearm |
| **2** | IN4 | **GPIO18** | P4.IO2 | CH8 | Finger extensors (grip open) | Dorsal (back) forearm |

Plus two single lines:

| Signal | GPIO | AX22 port | Connect to |
|---|---|---|---|
| TIMER keep-alive | **GPIO2** | P1.IO2 | HK4100F relay; its contacts wire across the **TIMER button of BOTH AUVON units**. Firmware pulses this pin for 250 ms every 5 min to simulate a button press. |
| Hardware e-stop | **GPIO9** ← **MOVED** | P3.IO0 | Normally-**closed** pushbutton to **GND** (opens = kill). *Was GPIO8 on the old board; GPIO8 is battery sense here.* |

Free port pins left over: **GPIO1** (P4.IO0) and **GPIO3** (P1.IO1). Use GPIO1
if you need a spare — GPIO3 is a strapping pin and a pull-down on it changes how
the board boots.

### On-board indicator (new — nothing to wire)

The NeoPixel on GPIO21 now reports firmware state, visible across a room:

| Colour | Meaning |
|---|---|
| dim blue | booting, no network yet |
| blue | Wi-Fi up, **disarmed** (safe) |
| yellow | armed, nothing stimulating |
| **red, pulsing** | **stimulating — current is flowing to the subject** |
| magenta, fast blink | killed / e-stopped (latched; needs re-arm) |
| orange, blinking | armed but the control link has gone quiet |

Red is used for "current flowing" rather than green-for-good on purpose: the
state a bystander most needs to recognise instantly is the one where a person is
being stimulated. **It is a convenience, not a safety device** — it is never
read by any control decision, and it is not permission to touch anyone. Set
`STATUS_LED_ENABLED = False` in `firmware/config/settings.py` to disable.

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

**Relay trigger polarity — verify, do not assume.** Our modules energise on
**HIGH**, so `CHANNEL_ACTIVE_LOW = False` in `firmware/config/pins.py`. The idle
state must leave the relay **released** (COM–NC, dummy resistor). If idle
energises the relay, COM–NO closes and the **subject** becomes the load at boot
and after every safety cutout. Meter COM–NO at boot: it must read open.

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

### Flash MicroPython — **the image changed with the board**

> **Do not reuse the old `.bin`.** The Goouuu board had *octal* PSRAM and
> needed the `SPIRAM_OCT` image. The Genesis Mini's ESP32-S3-Mini-1-**N4R2**
> has *quad* (QSPI) PSRAM and needs the **standard** image, which auto-detects
> PSRAM and explicitly supports MINI modules. Flashing `SPIRAM_OCT` here
> typically gives a board that enumerates over USB but never reaches the REPL.
>
> Also ignore the `FLASH_4M` variant, even though this board has 4 MB — the
> MicroPython download page marks it **obsolete** and says to use the standard
> one.

#### Download the image

PowerShell, from the project root — creates `bin\` if it does not exist:

```powershell
cd C:\Users\faisa\Desktop\juno_hack
mkdir bin -Force
Invoke-WebRequest `
  -Uri "https://micropython.org/resources/firmware/ESP32_GENERIC_S3-20260406-v1.28.0.bin" `
  -OutFile "bin\ESP32_GENERIC_S3-20260406-v1.28.0.bin"
```

cmd.exe instead:

```cmd
cd C:\Users\faisa\Desktop\juno_hack
if not exist bin mkdir bin
curl -L -o bin\ESP32_GENERIC_S3-20260406-v1.28.0.bin https://micropython.org/resources/firmware/ESP32_GENERIC_S3-20260406-v1.28.0.bin
```

Or from a browser: <https://micropython.org/download/ESP32_GENERIC_S3/> → the
**first** "Firmware" section (headed just `### Releases`, *not* "Support for
Octal-SPIRAM", *not* "4MiB flash") → newest `.bin`. Save it into `bin\`.

**Check it before flashing.** A truncated download or the wrong variant both
fail confusingly at flash time:

```powershell
Get-ChildItem bin\*.bin | Select-Object Name, Length
```

Expect roughly **1.6–1.8 MB**. A few KB means you saved an HTML error page. If
the filename contains `SPIRAM_OCT` or `FLASH_4M`, it is the wrong one — delete
it, so it cannot be picked up by mistake later.

#### Flash it

Find the port first — it changes between the bootloader and MicroPython, and it
will not be COM11/COM12 on a different board:

```powershell
mpremote connect list
```

> **esptool v5 renamed every command.** `erase_flash` → `erase-flash`,
> `write_flash` → `write-flash`, `flash_id` → `flash-id` — underscores became
> hyphens across the board, along with options like `--flash_size` →
> `--flash-size`. The old names still work in v5 but print a deprecation
> warning and will be removed next major release. They do **not** work the
> other way round: hyphenated commands fail on esptool v4.
>
> Check which you have — the commands below need **v5 or later**:
>
> ```powershell
> py -m esptool version
> ```
>
> If it reports v4.x: `py -m pip install --upgrade "esptool>=5.0"`

Hold **BOOT**, tap **RESET**, release **BOOT** (this is the same on the Genesis
Mini), then — substituting the port you just found:

```powershell
py -m esptool --chip esp32s3 --port COM8 erase-flash
py -m esptool --chip esp32s3 --port COM8 --baud 460800 write-flash -z 0 bin\ESP32_GENERIC_S3-20260406-v1.28.0.bin
```

If those two commands error with something like *"No such command
'erase-flash'"*, you are on esptool v4 — upgrade rather than reverting to the
underscore spelling, since the old names are on their way out.

Tap **RESET**, then confirm the right build actually landed before going
further — this is the one-command check that catches a wrong-variant flash:

```powershell
mpremote connect list          :: the port changes once MicroPython is running
mpremote exec "import sys, gc; print(sys.implementation); print('free RAM', gc.mem_free())"
```

Expect a version line and **free RAM of roughly 2 MB** — that is the QSPI PSRAM
being detected. A few hundred KB means PSRAM was not found; no REPL at all
usually means the `SPIRAM_OCT` image got flashed.

Known-good output on this board:

```
(name='micropython', version=(1, 28, 0, ''), _machine='Generic ESP32S3 module
 with ESP32S3', _mpy=11014, _build='ESP32_GENERIC_S3', _thread='GIL')
free RAM 2061072
```

`_build='ESP32_GENERIC_S3'` (no `SPIRAM_OCT` suffix) and ~2.06 MB free are the
two things to look at.

Then create the folders (WebREPL cannot make directories):

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

**First push onto a freshly flashed board** — verify it came up on the new pin
map before wiring anything:

```powershell
mpremote soft-reset
mpremote
```

**Known-good boot log on this board** (captured 2026-07-26, verbatim):

```
MPY: soft reboot
[boot] platform: MicroPython (ESP32-S3)
[boot] board: Axiometa Genesis Mini v1r2 (ESP32-S3-Mini-1-N4R2)
[boot] all channels de-energised (safe state)
[wifi] connected: 192.168.137.131
WebREPL server started on http://192.168.137.131:8266/
[webrepl] started - wireless deploy available
[boot] deploy wirelessly to 192.168.137.131 (WebREPL)
[main] platform: MicroPython (ESP32-S3)
[main] hardware e-stop armed on GPIO9 (debounced polling, 40 samples)
[net] UDP listening on :8080
[main] control token REQUIRED
[main] firmware 2026-07-26.9-genesis-mini
[main] ready - awaiting commands on 192.168.137.131:8080
[main] DISARMED at boot; PC must send {"arm":true} to enable stim
```

Four things to check, in this order:

1. **`board:` names the Genesis Mini.** If it does not, an old copy of
   `config/pins.py` is still on the board.
2. **`GPIO9`, not GPIO8.** GPIO8 is battery sense here.
3. **The firmware version ends `-genesis-mini`.** The same string comes back in
   every status heartbeat, so "is my new code actually running?" stays a
   one-second question.
4. **`DISARMED at boot`.** Nothing stimulates until the PC sends `arm`.

If the pin map is wrong for this board, `boot.py` raises before touching a
single output — a `ValueError` naming the offending GPIO instead of silently
driving something unexpected.

> **No `[led] disabled` line means the NeoPixel initialised.** With the board
> booted and disarmed it should be showing **solid blue**. If it is dark, the
> LED failed silently by design (it must never raise into the control loop) —
> harmless, but see `STATUS_LED_ENABLED`.

If the IP ever changes again, take it from `[wifi] connected:` in this log and
update `--host` below.

**Over Wi-Fi (no cable):**

```powershell
py tools/deploy_wifi.py --host 192.168.137.131
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
[wifi] connected: 192.168.137.131
[webrepl] started - wireless deploy available
[main] ready - awaiting commands on 192.168.137.131:8080
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
py tools/bench.py --host 192.168.137.131
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

**One command — vision + control together:**

```powershell
cd C:\Users\faisa\Desktop\juno_hack
py tools\launch.py --no-board                     # dry run, nothing stimulated
py tools\launch.py --host 192.168.137.131         # full system
```

It starts axon-main's pose service **and** the 3D twin page, waits for them,
hands the terminal to the controller (so the arrow keys work), and shuts
everything down when you quit. First run is slow — uv downloads mediapipe.
Add `--verbose` to watch it.

**The 3D twin opens in your browser automatically** at
<http://127.0.0.1:8081/twin.html>. Pass `--no-open` if you would rather it
didn't. The camera preview, if you want it, is at
<http://127.0.0.1:8000/camera.mjpeg>.

The pose service itself is headless — **no window appears on its own**; the twin
is a separate page that connects back to it over a WebSocket.

**To stop everything, press `Q` in the launcher's terminal.** Closing the
browser tabs does nothing — the browser is only a viewer. If the launcher window
was closed abruptly and something is still holding the camera or a port:

```powershell
py tools\stop.py -n     # show what is still running
py tools\stop.py        # stop it
```

**Or the pieces separately:**

```powershell
cd controller
py run.py --sim                                  # virtual arm, no hardware
py run.py --sim-hw --host 192.168.137.131        # virtual arm, REAL relays
py run.py --host 192.168.137.131                 # full system (needs pose feed)
```

**`--sim-hw` is hardware-in-the-loop** — the virtual arm closes the control
loop, but its duties go to the real board so relays and PWM actually switch.
No pose estimator required, so it is the way to shake out the whole chain
(PI → mapping → UDP → firmware PWM → relay contacts) before anyone is wired up.
The status line shows **`SIM+HW`** so you cannot mistake it for a dry run.

> **Relays fire in this mode. Nothing may be connected to a person.**

Keys, one axis per pair:

| Key | Moves | Muscle |
|---|---|---|
| **↑ / ↓** | raise / lower the arm | CH5 middle deltoid / **gravity** |
| **← / →** | swing forward / back | CH3 anterior / CH4 posterior deltoid |
| **W / S** | bend / straighten elbow | CH1 biceps / CH2 triceps |
| **G** | grip toggle | CH7 finger flexors |
| **A** arm · **X** e-stop · **?** help · **Q** quit | | |

**↓ fires no muscle.** There is no adductor channel (the natural one is
pectoralis major, which means chest electrodes near the heart), so the arm comes
down under its own weight and ↓ only does something if it is already raised.

**Reading the status line:** if a `db:` field appears (e.g. `db:e5`), the pose
feed has got noisy and the controller has widened that joint's deadband — the
arm will settle further short of its targets. It is silent when everything is
fine. That is your cue to check the camera, not the gains. See §5b.

---

## 5b. Check the pose quality (do this before wiring anyone up)

The camera setup matters more than any tuning constant — repeated 20 s captures
on this rig gave elbow noise of 10.9, 2.5, 3.3 and 7.3° with nothing in the code
changing. Measure before you trust the loop.

`run.py` binds UDP 9090, so it cannot be running at the same time. Use
**pose-only** mode, which starts the vision side and the 3D twin without the
controller:

```powershell
:: TERMINAL 1 - vision + twin, UDP 9090 left free. Leave this window open.
cd C:\Users\faisa\Desktop\juno_hack
.venv\Scripts\activate
py tools\launch.py --pose-only

:: TERMINAL 2 - measure. Subject holds ONE posture still for 20 s.
cd C:\Users\faisa\Desktop\juno_hack
.venv\Scripts\activate
py tools\pose_noise.py
```

The twin opens in your browser so you can confirm the subject really is still —
any real movement inflates the numbers.

Every capture is saved to `pose_capture.json`, so filter settings can be
re-tested without asking anyone to sit still again:

```powershell
py tools\pose_noise.py --replay --median 9 --mincutoff 0.10 --beta 0.005
```

Label captures to compare setups instead of overwriting them:

```powershell
py tools\pose_noise.py --label frontOn
:: turn the subject or camera ~45 deg, hold the SAME posture, then
py tools\pose_noise.py --label camera45
```

**What to do with the output:**

| It says | Do this |
|---|---|
| `burst` above 2 | Tracking dropouts, not noise. Re-launch with `py tools\launch.py --min-visibility 0.7` so axon-main drops low-confidence frames instead of guessing. |
| `DEPTH IS THE DOMINANT ERROR` | Turn the subject or camera ~45°. Costs nothing, beats any filter. |
| `DRIFT/MOVE` | The subject moved, or the estimator is wandering. Re-capture holding properly still. |
| `deadband needed` above 3.0 | Nothing to edit — the controller sizes its own deadband live. Treat it as a measure of how good your camera setup is. |

You do **not** need to copy any recommended deadband into `settings.py`. The
values in `GAINS` are floors; the controller measures the live noise and raises
the deadband itself. Fix the camera and it tightens on its own.

---

## 6. Offline checks (no hardware)

```powershell
cd controller
py test_simulation.py
```

Expect `151 checks, 0 failed`.

---

## Troubleshooting, this machine

| Symptom | Fix |
|---|---|
| `python` not recognised | Activate the venv, or use `py`. |
| `mpremote`/`esptool` not found | venv not active — re-run the activate line. |
| `mpremote` hangs, no output | MicroPython not running. Re-flash (section 1). |
| Connected but Enter/Ctrl-D do nothing | `main.py` is looping — press **Ctrl-C**. |
| `failed to access COM12` | You pressed the physical RESET while attached. Ctrl-], wait 5 s, reconnect. |
| esptool "could not open COM8" | Board is not in download mode — hold BOOT, tap RESET, release BOOT. |
| `[deploy] board not found` | Firewall (rule in section 1), or the IP changed. Check **Settings → Mobile hotspot → connected devices**, then use `--host <new-ip>`. |
| Every deploy file FAILs | Run with `-v` for the real error; USB fallback: `mpremote fs cp -r firmware/. :` |
| `run.py` connects and arms, but no relay ever fires (bench.py works) | Fixed. The board rejected packets whose sequence number was lower than the last seen, and every tool restarts its counter at 1 — so after a bench session, `run.py` was ignored. Re-deploy `lib/net_udp.py`. |
| A channel "does nothing" | An active channel **buzzes at ~6.7 Hz**, it does not click or sit closed — `DUTY_MAX` is 0.70, so it is 105 ms on / 45 ms off. Use `bench> click 1 5` for an audible 0.5 s on / 0.5 s off pattern. |
| `board:KILLED(hardware_estop)` but nobody pressed the button | Relay-coil noise coupling into the e-stop lead (**GPIO9** on this board). Fixed in firmware `2026-07-25.6-estop-noise` (edge interrupt removed, 40 ms debounce). If it recurs, fix it in hardware: **100 nF from GPIO9 to GND**, and route the e-stop lead away from the coil wiring. See [`docs/WIRING.md`](docs/WIRING.md). |
| `Operations on 2 remote files are not supported` | Fixed. `webrepl_cli.py` treats any argument containing `:` as remote, so a Windows absolute path (`C:\...`) confused it; `deploy_wifi.py` now passes relative paths. Update your copy if you still see this. |
| Board never joins Wi-Fi | Hotspot must be **2.4 GHz** (Properties → Band) and its **Network name must equal `faisal_network`**, or edit `WIFI_SSID` in `firmware/config/device_secrets.py` and re-push. |

**If the board's IP changes:** Settings → Network & internet → Mobile hotspot →
connected devices (its MAC is `CC:BA:97:05:11:A8`). Update the IP in this file.
