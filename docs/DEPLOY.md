# Wireless deployment

Goal: the board runs **untethered**, powered from its 5 V pin, with code pushed
over Wi-Fi. USB is needed exactly once, to flash MicroPython.

> **Every command below assumes the virtual environment is active.** From the
> repository root:
>
> ```powershell
> .venv\Scripts\Activate.ps1      # Windows PowerShell
> ```
> ```bash
> source .venv/bin/activate       # macOS / Linux
> ```
>
> Your prompt should show `(.venv)`.
>
> **On Windows use `py` instead of `python` if your system does not recognise
> `python`.** Activating the venv normally makes `python` work regardless; if it
> still does not, substitute `py` (or `.venv\Scripts\python.exe`) in every
> command below. First-time setup:
> [README](../README.md#0-set-up-the-virtual-environment-once).

---

## Network layout

Both the board and the control PC join **your mobile hotspot** (station mode),
rather than the board hosting its own AP. That way the PC keeps its normal
network, the board is reachable for wireless deploys, and no cable is required.

```
        mobile hotspot "faisal_network"
           |                      |
      control PC            ESP32-S3 (DHCP)
   controller/run.py     firmware + WebREPL
           |                      |
           +---- UDP :8080 -------+   control link
           +---- WebREPL :8266 ---+   code deploy
```

The board's DHCP address is **discovered automatically** — the PC broadcasts a
`{"discover": true}` ping and the board answers. Nothing is hard-coded and you
never need to read an IP off a serial console.

Credentials live in `firmware/config/device_secrets.py`, which is **gitignored**.
Copy `device_secrets.example.py` if you need to recreate it.

---

## One-time USB setup

You need a cable for this stage only.

### 1. Flash MicroPython

**Download the firmware first — it is not in this repo.** You need the
**`SPIRAM_OCT`** variant: this board is an **N16R8**, which has *octal* PSRAM,
and the plain `ESP32_GENERIC_S3` build will not use it correctly.

Download page: <https://micropython.org/download/ESP32_GENERIC_S3/> — scroll to
the section headed **"Firmware (Support for Octal-SPIRAM)"** and take the
newest `.bin`.

At the time of writing that is **v1.28.0 (2026-04-06)**:

```
ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin
https://micropython.org/resources/firmware/ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin
```

Below, `<FIRMWARE.bin>` means **the full path to the file you just downloaded**,
e.g. `%USERPROFILE%\Downloads\ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin`.
Check the version on the site rather than copying the filename above verbatim.

> Take the file with **`SPIRAM_OCT` in its name**. A plain
> `ESP32_GENERIC_S3-<date>-<version>.bin` is the wrong variant for this board.

Install the flashing tools into the venv (once):

```powershell
py -m pip install -r requirements.txt
```

### Find your board's serial port first

**Do not assume a port number.** Every example below writes `<PORT>` — substitute
what the next command reports. It differs per machine, per USB socket, and per
board (this ESP32-S3 uses a different USB-serial chip from most ESP32 boards).

```powershell
mpremote connect list
```

Typical output — the line with an ESP32/CP210x/CH340/USB JTAG description is
your board:

```
COM3 None 0000:0000 None None
COM7 7&2f8b1c9d&0&2 303a:1001 Espressif USB JTAG/serial debug unit
```

Here the port is `COM7`. If nothing is listed, unplug and re-plug the board and
run it again; if it still does not appear, you need the USB-serial driver
(CP210x or CH340, depending on the board) or a different cable — **some USB
cables are charge-only and carry no data.**

On macOS/Linux the port looks like `/dev/ttyUSB0`, `/dev/ttyACM0`, or
`/dev/cu.usbmodem101`. You can also list them with:

```bash
ls /dev/tty*        # Linux
ls /dev/cu.*        # macOS
```

Put the board into download mode: **hold BOOT, tap RESET, release BOOT.**

The module form (`py -m esptool`) is used here because it works whether or not
the `esptool` / `esptool.py` console script ends up on your PATH:

```powershell
py -m esptool --chip esp32s3 --port <PORT> erase_flash
py -m esptool --chip esp32s3 --port <PORT> --baud 460800 write_flash -z 0 <FIRMWARE.bin>
```

On macOS / Linux:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyUSB0 erase_flash
python -m esptool --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash -z 0 <FIRMWARE.bin>
```

### 2. Create the directories and push the tree

> **The COM port changes after flashing — and that is normal.** On this board the
> USB device is provided by the ESP32-S3 itself, and the **ROM bootloader and
> MicroPython enumerate as different USB devices**. So the port you flashed on
> (e.g. `COM11`) is often *not* the port MicroPython appears on (e.g. `COM12`),
> and it will flip back when you next hold BOOT to re-flash.
>
> **Simplest solution: leave the port out.** `mpremote` auto-detects the board,
> so it follows the port around by itself. Every `mpremote` command below omits
> `connect <PORT>` for that reason. (Only specify a port if you have two boards
> plugged in at once.)

WebREPL can copy files but **cannot create folders**, so the two folders have to
be made once over USB.

In `mpremote`, a leading `:` means *the board's own filesystem*. So `:lib` is a
folder called `lib` in the board's root directory — not a folder on your PC.

```bash
mpremote fs mkdir :lib
mpremote fs mkdir :config
mpremote fs cp -r firmware/. :
```

If auto-detect picks the wrong device, fall back to an explicit port —
`mpremote connect list`, then `mpremote fs ...`.

Confirm the files landed:

```bash
mpremote fs ls :
mpremote fs ls :lib
mpremote fs ls :config
```

Expected: `boot.py` and `main.py` in the root; `hal.py`, `net_udp.py`,
`safety.py`, `stim_array.py`, `stim_channel.py`, `wifi_manager.py` in `lib`;
`pins.py`, `settings.py`, `device_secrets.py`, `__init__.py` in `config`.

### 3. Verify it joins the hotspot

With the hotspot on, connect and watch the console:

```bash
mpremote
```

Reboot the board. The reliable way is a single command:

```bash
mpremote soft-reset
```

Then `mpremote` to attach and watch the log.

> Once `main.py` is on the board it runs an endless control loop, so there is
> **no `>>>` prompt** and Enter appears to do nothing. That is normal. Press
> **Ctrl-C** to interrupt the loop and get a prompt; `Ctrl-D` then restarts the
> firmware from the top.

> **Never press the physical RESET button while mpremote is attached.** This
> board's USB is implemented by the ESP32-S3 itself (`303a:1001`), not by a
> separate USB-serial chip, so a hardware reset makes the COM port disappear and
> re-enumerate — mpremote is left with a dead handle and reports *"failed to
> access ... it may be in use by another program"*. `Ctrl-D` restarts
> `boot.py`/`main.py` without dropping the USB connection.

Expected:

```
[boot] all channels de-energised (safe state)
[wifi] connecting to 'faisal_network' ...
[wifi] connected: 192.168.43.xxx
[webrepl] started - wireless deploy available
[main] DISARMED at boot; PC must send {"arm":true} to enable stim
```

Unplug the USB cable. You are done with it.

---

## Powering from the 5 V pin

- Feed a regulated **5 V** supply into the board's `5V` (or `VIN`) pin and tie
  grounds together. The onboard regulator drops it to 3.3 V.
- **Do not power from 5 V and USB simultaneously** unless the board has proper
  input isolation — you risk back-feeding the host port.
- The relay modules should share the same 5 V supply for their coils, with a
  common ground to the ESP32.
- Budget the supply: 8 relay coils at ~70–90 mA each plus the ESP32's Wi-Fi
  peaks means **at least 1.5–2 A**. An undersized supply causes brownouts that
  look exactly like random firmware crashes.
- With USB gone there is no serial console. Use the WebREPL terminal for logs.

---

## Everyday wireless deploys

```bash
python tools/deploy_wifi.py                 # auto-discovers the board
python tools/deploy_wifi.py --host 192.168.43.50
python tools/deploy_wifi.py --reset
```

Then reboot the board so it runs the new code. Either power-cycle it, or soft
reset over WebREPL:

1. Open <http://micropython.org/webrepl/> in a browser.
2. In the address box type `ws://192.168.43.xxx:8266` (your board's IP) and
   click **Connect**.
3. Enter the WebREPL password (`WEBREPL_PASSWORD` in `device_secrets.py`).
4. Press **Ctrl-D** at the `>>>` prompt to soft-reset.

The boot log then appears in that same terminal — this is your console now that
USB is unplugged.

To check what is on the board without a soft reset, press **Ctrl-C** at the
`>>>` prompt to stop `main.py`, then:

```python
import os
os.listdir()
os.listdir('lib')
```

Type `import main; main.run()` to start the control loop again, or Ctrl-D to
reboot cleanly.

**WebREPL is started in `boot.py`, before `main.py`.** That is deliberate: if
`main.py` crashes, the board is still on the network and still updatable. You
should never need the cable again to fix a software bug.

---

## Running the demo

```bash
cd controller
python run.py
```

It prints `[link] found board at <ip>` on success. Press `A` to arm.

If the hotspot is unavailable, the board falls back to hosting its own AP
(`juno-fes` / `juno12345` at `192.168.4.1`) so the demo still runs — join that
network from the PC and `run.py` will find it there.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `[wifi] TIMEOUT joining` | Hotspot off, wrong credentials, or 5 GHz-only. **The ESP32 is 2.4 GHz only** — see below. |
| `[deploy] board not found` | See "board not found" below — usually the PC is not on the same network as the board. |
| Deploy fails on `lib/...` | Directories missing. Create them over USB (step 2) — WebREPL cannot mkdir. |
| **`mpremote` hangs forever with no output** | It waits for a MicroPython REPL and never times out. Ctrl-C, then see "mpremote hangs" below. |
| `failed to access <PORT> (it may be in use by another program)` | Usually caused by pressing the **physical RESET** while mpremote is attached — this board's USB is provided by the chip itself, so the port vanishes and re-enumerates. Exit (`Ctrl-]`), wait ~5 s, reconnect; re-plug the cable if it persists. **Use `Ctrl-D` (soft reset) instead of the RESET button while connected.** Also check no other terminal/serial monitor holds the port. |
| Connected but no `>>>` prompt | Normal — mpremote attaches silently. **Press Enter.** |
| Connected, but Enter/Ctrl-D do nothing | `main.py` is running its control loop, so there is no prompt. **Press Ctrl-C** to break in, then Enter for `>>>`. (Ctrl-D soft-resets, which just restarts `main.py`.) |
| `mpremote`/`esptool` not found | The venv is not active. Re-run the activate command; the prompt must show `(.venv)`. Or use the module form: `py -m esptool ...`. |
| `python` is not recognised (Windows) | Use `py` instead, or activate the venv first — activation puts `python` on your PATH. Last resort: `.venv\Scripts\python.exe`. |
| `ModuleNotFoundError` running a tool | Wrong interpreter — outside the venv, or a different Python. Check `python -c "import sys; print(sys.prefix)"` points inside `.venv`. |
| Board resets when relays fire | Power supply too small or coils drawing through the board. Use a ≥2 A supply and power coils separately. |
| Wi-Fi drops mid-session | Commands stop → watchdog opens every relay (intended). `main.py` reconnects, but you must **re-arm deliberately**. |
| Discovery works, control does not | Firewall on the PC blocking UDP 8080. |
| No console output | Expected without USB — use the WebREPL terminal (see above). |

### Forcing the hotspot to 2.4 GHz

The ESP32-S3 has **no 5 GHz radio**. If your phone's hotspot is 5 GHz or
"auto", the board will never connect. This is the single most common failure.

- **Android:** Settings → Network & internet → Hotspot & tethering →
  Wi-Fi hotspot → *AP Band* → select **2.4 GHz**.
- **iPhone:** Settings → Personal Hotspot → turn **Maximise Compatibility** ON
  (this forces 2.4 GHz).

Also turn **off** any "client isolation" / "isolate devices" option, or the PC
and board will be on the same network but unable to talk to each other.

### `mpremote` hangs with no output

`mpremote` does not talk to the chip directly — it talks to the **MicroPython
interpreter running on** the chip, and it waits **indefinitely** rather than
timing out. A hang almost always means "no REPL is answering", not a bad cable.

Note that the port appearing in `mpremote connect list` proves nothing: the
ESP32-S3's native USB (`303a:1001`) enumerates whether or not MicroPython is
installed.

Press **Ctrl-C**, then work down this list:

**1. Is MicroPython actually installed?** (The most common cause — this step is
easy to skip.)

```powershell
mpremote
```

Tap the board's **RESET** button. You want a `>>>` prompt; `Ctrl-]` exits. No
prompt ⇒ MicroPython is not installed. Flash it (see "Flash MicroPython" above).

**2. Is the board stuck in download/bootloader mode?** If BOOT was held, no REPL
is running. Tap **RESET** on its own to leave that mode.

**3. Is another program holding the port?** Arduino IDE serial monitor, PuTTY, a
second `mpremote`. Windows sometimes hangs instead of reporting "port busy".

**4. Confirm the board itself is alive.** This uses the ROM bootloader, so it
answers even on a completely blank chip:

```powershell
py -m esptool --chip esp32s3 --port <PORT> flash_id
```

If `flash_id` responds but `mpremote` still hangs, the hardware and cable are
fine and the firmware is the problem — flash MicroPython.

**5. Later on, a busy `main.py` can also block it.** Once firmware is running,
`mpremote` needs the REPL to be responsive. Interrupt with `Ctrl-C`, or connect
and press `Ctrl-C` to break into a running `main.py`.

### Using the laptop itself as the hotspot (Windows Mobile Hotspot)

This works and is convenient — one less device — but it behaves differently
from a phone hotspot in ways that break naive discovery:

- Windows puts the shared network on a **separate virtual adapter** at
  **`192.168.137.1`**, and clients (your board) get **`192.168.137.x`**.
- The laptop's *default route* still points at its upstream connection (e.g.
  `10.0.14.x` on university Wi-Fi), so tools that follow the default route look
  on entirely the wrong network. Discovery therefore probes the standard
  hotspot ranges explicitly, `192.168.137.x` included.
- **Windows Firewall is the most common blocker here**, and it produces a very
  specific symptom: the board's boot log shows it connected fine and
  `[net] UDP listening on :8080`, yet discovery reports "no board responded".
  The shared adapter is classified *Public*, where inbound UDP is dropped. Fix
  it once, in an **Administrator** PowerShell:

  ```powershell
  New-NetFirewallRule -DisplayName "Juno FES UDP" -Direction Inbound `
      -Protocol UDP -LocalPort 8080,9090 -Action Allow -Profile Any
  ```

  Until then, skip discovery with `--host <board-ip>`.
- **Find the board's IP without the USB cable:** Windows lists attached clients
  under **Settings → Network & internet → Mobile hotspot** with their IP and
  MAC. Handy once the board is running untethered.
- Set the hotspot band to **2.4 GHz** (Settings → Mobile hotspot → Properties →
  Band). The ESP32-S3 has no 5 GHz radio.

`WIFI_SSID` / `WIFI_PASSWORD` in `firmware/config/device_secrets.py` must match
the laptop hotspot's name and password.

### "board not found" from deploy_wifi.py / run.py

Discovery prints which subnets it swept. **Compare that with the board's own IP**
— if they are different networks, that is your answer.

**1. Get the board's IP.** Over USB: `mpremote`, then **Ctrl-D**, and read the
boot log:

```
[wifi] connected: 192.168.43.137
```

**2. Check the PC is on that same network.**

```powershell
ipconfig
```

Look at the **Wireless LAN adapter Wi-Fi** entry. If the board says
`192.168.43.x` but your PC says `10.0.14.x` (or similar), the PC is **not joined
to the hotspot** — it is on ethernet, university/corporate Wi-Fi, or a VPN.
Join `faisal_network` on the PC and retry.

A laptop can hold several networks at once; discovery sweeps every interface it
can see, but it cannot reach a network the PC is not attached to.

**3. Disconnect the VPN.** A VPN captures the default route and often blocks
local-subnet traffic entirely, which breaks both broadcast and the sweep.

**4. Turn off hotspot client isolation.** Some phones stop clients talking to
each other (see the 2.4 GHz section above). The board will have an IP but be
unreachable from the PC.

**5. Allow Python through the Windows firewall** (private networks). It can
silently drop the inbound replies.

**6. Bypass discovery entirely** once you know the IP:

```powershell
python tools/deploy_wifi.py --host 192.168.43.137
python controller/run.py                      # uses the same discovery
```

### Verifying the board is reachable

```bash
ping 192.168.43.xxx
python tools/bench.py --host 192.168.43.xxx
```

If `ping` works but `bench.py` does not, a firewall on the PC is blocking
UDP 8080. On Windows, allow Python through the private-network firewall.
