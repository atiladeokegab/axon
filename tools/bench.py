#!/usr/bin/env python3
"""Interactive bench tester - fire individual channels by hand.

This is the tool for hardware bring-up (docs/TESTING.md stages 2 and 3). It
talks straight to the board over UDP and bypasses the control loop entirely, so
you can verify wiring one relay at a time.

    python tools/bench.py
    python tools/bench.py --host 192.168.137.154

Commands at the prompt:
    arm                 enable stimulation (required before any channel fires)
    disarm              disable stimulation
    click 1 5           channel 1: 5 slow ON/OFF cycles, ~1/sec  <-- audible
    pulse 2 0.7 3       channel 2 at 70% duty for 3 seconds  <-- PWM (buzzes)
    all 0.3 5           every channel at 30% for 5 seconds
    watchdog            hold, then go silent - proves relays open on link loss
    off                 all channels to zero
    timer               fire the TIMER keep-alive relay once
    status              show the board's last heartbeat
    kill                latching e-stop
    q                   quit (sends kill on the way out)

    1 0.5 / ch3 0.7     single packet only - holds for <500 ms, then the
                        watchdog opens the relay. Fine for a quick click test,
                        but use `pulse` to hold a channel on.

NOTE: the board's watchdog opens every relay after 500 ms without a command, so
any sustained output must be re-sent continuously. `pulse`, `all` and
`watchdog` do that for you; a bare `1 0.5` does not.

SAFETY: `arm` enables real stimulation. Do this with nothing connected to a
person until you have completed the checks in docs/TESTING.md.
"""

import argparse
import json
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "controller"))

CHANNELS = 8

# Shared token required by the firmware. Read from the controller settings so
# there is one place to change it.
try:
    from settings import CONTROL_TOKEN as TOKEN
except Exception:
    TOKEN = None


class Bench:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.seq = 0
        self.duty = [0.0] * CHANNELS
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)

    def send(self, **extra):
        self.seq += 1
        msg = {"duty": self.duty, "seq": self.seq}
        if TOKEN:
            msg["tok"] = TOKEN
        msg.update(extra)
        self.sock.sendto(json.dumps(msg).encode(), (self.host, self.port))

    def keepalive(self):
        """The board's watchdog opens all relays after 500 ms of silence, so a
        held duty must be re-sent continuously."""
        self.send()

    def status(self):
        out = None
        while True:
            try:
                data, _ = self.sock.recvfrom(512)
            except (OSError, BlockingIOError):
                break
            try:
                out = json.loads(data.decode())
            except Exception:
                pass
        return out


def resolve_channel(token):
    t = token.lower().lstrip("c").lstrip("h")
    if not t.isdigit():
        return None
    idx = int(t)
    return idx - 1 if 1 <= idx <= CHANNELS else None


def main():
    ap = argparse.ArgumentParser(description="FES bench tester")
    ap.add_argument("--host", help="board IP (default: auto-discover)")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    host = args.host
    if not host:
        try:
            from link import discover
            host = discover(port=args.port)
        except ImportError:
            pass
    if not host:
        sys.exit("Board not found. Pass --host <ip>.")

    b = Bench(host, args.port)
    print(__doc__.split("Commands at the prompt:")[0])
    print("Connected to %s:%d. Type 'help' for commands.\n" % (host, args.port))

    armed = False
    try:
        while True:
            try:
                raw = input("bench> ").strip()
            except EOFError:
                break
            if not raw:
                b.keepalive()
                continue
            parts = raw.split()
            cmd = parts[0].lower()

            if cmd in ("q", "quit", "exit"):
                break
            elif cmd == "help":
                print(__doc__)
            elif cmd == "arm":
                b.send(arm=True)
                # Confirm against the board's heartbeat. Printing "ARMED" purely
                # because we transmitted a packet is misleading: if main.py is
                # not running (e.g. someone pressed Ctrl-C at the REPL) the
                # command goes nowhere and the tool would still claim success.
                time.sleep(0.4)
                st = b.status()
                if st is None:
                    armed = False
                    print("SENT arm, but the board did not reply.")
                    print("  Is main.py running? Ctrl-C at the REPL halts it -")
                    print("  press Ctrl-D in mpremote to restart the firmware.")
                elif st.get("killed"):
                    armed = False
                    print("Board reports KILLED (fault=%s) - not armed."
                          % st.get("fault"))
                    if st.get("estop"):
                        print("  e-stop line is OPEN (pressed or miswired).")
                else:
                    armed = bool(st.get("armed"))
                    print("ARMED - confirmed by board" if armed
                          else "Board did NOT arm: %s" % st)
            elif cmd == "disarm":
                b.duty = [0.0] * CHANNELS
                b.send(disarm=True)
                armed = False
                print("disarmed")
            elif cmd == "kill":
                b.duty = [0.0] * CHANNELS
                for _ in range(5):
                    b.send(kill=True)
                armed = False
                print("KILLED (latched) - type 'arm' to re-enable")
            elif cmd == "off":
                b.duty = [0.0] * CHANNELS
                b.send()
                print("all channels 0.0")
            elif cmd == "timer":
                b.send(timer_press=True)
                print("TIMER relay pulsed once")
            elif cmd == "status":
                b.send()
                time.sleep(0.4)
                st = b.status()
                if st is None:
                    print("(no reply from the board)")
                    print("  1. Is main.py running? Ctrl-C at the REPL stops it;")
                    print("     press Ctrl-D in mpremote to restart it.")
                    print("  2. Is inbound UDP 8080 allowed through the firewall?")
                    print("  3. Is the board powered and on this network?")
                else:
                    print(st)
                    print("  firmware on board: %s" % st.get("fw", "(pre-versioning "
                          "build - your deploy or reboot did not take)"))
                    if st.get("estop"):
                        print("  NOTE: e-stop line OPEN (pressed or miswired)"
                              " - stimulation is blocked.")
            elif cmd == "all" and len(parts) in (2, 3):
                d = float(parts[1])
                secs = float(parts[2]) if len(parts) == 3 else 5.0
                if not armed:
                    print("  NOT ARMED - nothing will fire. Type 'arm' first.")
                    continue
                # Must keep RE-SENDING. A single packet only holds for the
                # 500 ms watchdog window, then every relay opens by itself -
                # which looks like "the board stopped working".
                b.duty = [d] * CHANNELS
                print("all channels -> %.2f for %.1fs ..." % (d, secs))
                end = time.monotonic() + secs
                while time.monotonic() < end:
                    b.keepalive()
                    time.sleep(0.1)
                b.duty = [0.0] * CHANNELS
                b.send()
                print("done (all channels back to 0.0)")

            elif cmd == "click" and len(parts) in (2, 3):
                # Slow, unmistakable on/off toggling THROUGH the normal firmware
                # path (apply -> safety -> StimChannel -> pin).
                #
                # WHY THIS EXISTS: at duty 0.7 the relay switches 105ms on /
                # 45ms off - about 6.7 times a second. That is a buzz, not
                # clicks, and it is easy to conclude "nothing is happening" when
                # the channel is in fact working perfectly. This command gives
                # one clean click per second so the firmware path can be
                # confirmed by ear.
                idx = resolve_channel(parts[1])
                n = int(parts[2]) if len(parts) == 3 else 5
                if idx is None:
                    print("bad channel")
                    continue
                if not armed:
                    print("  NOT ARMED - type 'arm' first.")
                    continue
                print("CH%d: %d cycles of 0.5s ACTIVE / 0.5s silent."
                      % (idx + 1, n))
                print("  Listen for: half a second of rapid buzzing/chattering,")
                print("  then half a second of silence, repeating.")
                print("  NOTE: the relay never sits solidly closed - the safety")
                print("  layer clamps duty to %.2f, so an 'on' channel is really"
                      % 0.70)
                print("  105ms on / 45ms off at ~6.7Hz. A buzz IS correct.")
                for c in range(n):
                    b.duty = [0.0] * CHANNELS
                    b.duty[idx] = 1.0            # full duty = solid ON
                    t_end = time.monotonic() + 0.5
                    while time.monotonic() < t_end:
                        b.keepalive()
                        time.sleep(0.05)
                    print("  cycle %d: ACTIVE (should buzz)" % (c + 1))
                    b.duty = [0.0] * CHANNELS
                    t_end = time.monotonic() + 0.5
                    while time.monotonic() < t_end:
                        b.keepalive()
                        time.sleep(0.05)
                    print("  cycle %d: silent" % (c + 1))
                b.send()
                print("done")

            elif cmd == "watchdog":
                # Proper watchdog test: hold every channel, then GO SILENT
                # without sending anything else. The board must open all relays
                # ~500 ms later purely because commands stopped arriving.
                #
                # Ctrl-C is NOT a valid way to test this: the tool catches it
                # and sends an explicit kill, so you would be testing the kill
                # path instead of the watchdog.
                if not armed:
                    print("  NOT ARMED - type 'arm' first.")
                    continue
                b.duty = [0.7] * CHANNELS
                print("Holding all channels at 0.70 for 3s, then going SILENT.")
                print("LISTEN: every relay must open ~500ms after the silence.")
                end = time.monotonic() + 3.0
                while time.monotonic() < end:
                    b.keepalive()
                    time.sleep(0.1)
                print("...silent now. Relays should open within 500 ms.")
                time.sleep(2.0)
                st = b.status()
                b.duty = [0.0] * CHANNELS
                if st is not None and st.get("watchdog_expired"):
                    print("PASS: board reports watchdog_expired=True")
                else:
                    print("Check by ear/meter; board said: %s" % st)
            elif cmd == "pulse" and len(parts) == 4:
                idx, d, secs = resolve_channel(parts[1]), float(parts[2]), float(parts[3])
                if idx is None:
                    print("bad channel")
                    continue
                if not armed:
                    print("  NOT ARMED - nothing will fire. Type 'arm' first.")
                    continue
                b.duty = [0.0] * CHANNELS
                b.duty[idx] = d
                print("CH%d at %.2f for %.1fs ..." % (idx + 1, d, secs))
                print("  scope: active-LOW, so expect ~%dms at 0V then ~%dms at 3.3V"
                      % (int(d * 150), int((1 - d) * 150)))
                end = time.monotonic() + secs
                while time.monotonic() < end:
                    b.keepalive()          # must re-send or the watchdog fires
                    time.sleep(0.1)
                b.duty[idx] = 0.0
                b.send()
                print("done")
            else:
                idx = resolve_channel(cmd)
                if idx is None or len(parts) != 2:
                    print("unknown command - type 'help'")
                    continue
                b.duty = [0.0] * CHANNELS
                b.duty[idx] = float(parts[1])
                b.send()
                print("CH%d -> %.2f%s" % (idx + 1, b.duty[idx],
                                          "" if armed else "  (NOT ARMED - no output)"))
                print("  note: held only ~500ms unless re-sent; use 'pulse' to hold")
    except KeyboardInterrupt:
        pass
    finally:
        b.duty = [0.0] * CHANNELS
        for _ in range(5):
            b.send(kill=True)
        print("\nSent kill - all relays open.")


if __name__ == "__main__":
    main()
