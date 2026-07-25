#!/usr/bin/env python3
"""Deploy the firmware tree to the board over Wi-Fi - no USB cable needed.

Uses WebREPL (started by boot.py), so the board can sit powered from its 5 V
pin anywhere in the room while you push code to it.

Usage:
    python tools/deploy_wifi.py                  # auto-discover the board
    python tools/deploy_wifi.py --host 192.168.43.50
    python tools/deploy_wifi.py --password juno2026
    python tools/deploy_wifi.py --reset          # reboot the board afterwards

Requires tools/webrepl_cli.py (bundled).

FIRST TIME ONLY: the board needs MicroPython flashed over USB once, plus this
firmware tree copied so boot.py can bring up Wi-Fi. After that, every update is
wireless. See docs/DEPLOY.md.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIRMWARE = os.path.join(ROOT, "firmware")
WEBREPL_CLI = os.path.join(HERE, "webrepl_cli.py")

# Order matters: directories must exist on the board before files land in them.
# device_secrets.py IS deployed (the board needs credentials) but is gitignored.
FILES = [
    ("boot.py", "boot.py"),
    ("main.py", "main.py"),
    ("config/__init__.py", "config/__init__.py"),
    ("config/pins.py", "config/pins.py"),
    ("config/settings.py", "config/settings.py"),
    ("config/device_secrets.py", "config/device_secrets.py"),
    ("lib/__init__.py", "lib/__init__.py"),
    ("lib/hal.py", "lib/hal.py"),
    ("lib/safety.py", "lib/safety.py"),
    ("lib/stim_channel.py", "lib/stim_channel.py"),
    ("lib/stim_array.py", "lib/stim_array.py"),
    ("lib/net_udp.py", "lib/net_udp.py"),
    ("lib/wifi_manager.py", "lib/wifi_manager.py"),
    ("lib/netstate.py", "lib/netstate.py"),
]


def discover(port=8080, timeout_s=4.0):
    """Find the board on the network - reuses the controller's implementation."""
    sys.path.insert(0, os.path.join(ROOT, "controller"))
    try:
        from link import discover as _discover
        return _discover(port=port, timeout_s=timeout_s, verbose=True)
    except ImportError:
        pass

    # Standalone fallback (broadcast only) if the controller package is absent.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.4)
    payload = json.dumps({"discover": True}).encode()
    deadline = time.monotonic() + timeout_s
    print("[deploy] searching for the board ...")
    try:
        while time.monotonic() < deadline:
            for target in ("255.255.255.255", "192.168.4.1"):
                try:
                    sock.sendto(payload, (target, port))
                except OSError:
                    pass
            try:
                data, addr = sock.recvfrom(512)
            except (socket.timeout, OSError):
                continue
            try:
                if json.loads(data.decode()).get("juno"):
                    print("[deploy] found board at %s" % addr[0])
                    return addr[0]
            except Exception:
                continue
    finally:
        sock.close()
    return None


def put(host, password, local_rel, remote, verbose=False):
    """Push one file via webrepl_cli.py.

    `local_rel` MUST be relative (e.g. "lib/hal.py") and the subprocess runs
    with cwd=FIRMWARE. This is not cosmetic: webrepl_cli.py decides whether an
    argument is a remote path by testing `":" in arg`, so a Windows absolute
    path like C:\\Users\\... is misread as remote and it aborts with
    "Operations on 2 remote files are not supported".

    Also note webrepl_cli.py reports most failures on STDOUT, not stderr, so
    both streams have to be inspected.
    """
    cmd = [sys.executable, WEBREPL_CLI, "-p", password,
           local_rel, "%s:/%s" % (host, remote)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                cwd=FIRMWARE)
        ok = result.returncode == 0
        detail = (result.stderr.strip() or result.stdout.strip())
    except subprocess.TimeoutExpired:
        ok, detail = False, "timed out after 30s (board not responding on :8266)"

    if ok:
        print("  OK   %s" % remote)
    else:
        # Strip webrepl_cli's noisy banner line so the real error is visible.
        lines = [l for l in detail.splitlines()
                 if l.strip() and not l.startswith("op:")
                 and not l.startswith("Remote WebREPL version")]
        msg = lines[-1] if lines else "(no output)"
        print("  FAIL %-30s %s" % (remote, msg[:90]))
        if verbose and detail:
            for l in detail.splitlines():
                print("        | %s" % l)
    return ok


def main():
    ap = argparse.ArgumentParser(description="Wireless firmware deploy")
    ap.add_argument("--host", help="board IP (default: auto-discover)")
    ap.add_argument("--password", default="juno2026", help="WebREPL password")
    ap.add_argument("--reset", action="store_true",
                    help="soft-reboot the board after deploying")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show full webrepl_cli output for failures")
    args = ap.parse_args()

    if not os.path.exists(WEBREPL_CLI):
        sys.exit("[deploy] missing %s" % WEBREPL_CLI)

    secrets = os.path.join(FIRMWARE, "config", "device_secrets.py")
    if not os.path.exists(secrets):
        sys.exit("[deploy] create firmware/config/device_secrets.py first "
                 "(copy device_secrets.example.py)")

    host = args.host or discover()
    if not host:
        sys.exit("[deploy] board not found. Is it powered and on the hotspot?\n"
                 "         Try: python tools/deploy_wifi.py --host <ip>")

    print("[deploy] pushing firmware to %s" % host)
    failures = 0
    for local_rel, remote in FILES:
        if not os.path.exists(os.path.join(FIRMWARE, local_rel)):
            print("  SKIP %s (not found)" % local_rel)
            continue
        # Pass the RELATIVE path (cwd=FIRMWARE inside put) - see put()'s
        # docstring for why an absolute Windows path breaks webrepl_cli.
        if not put(host, args.password, local_rel, remote, verbose=args.verbose):
            failures += 1

    if failures:
        print("\n[deploy] %d file(s) failed." % failures)
        if failures == len(FILES):
            # Everything failed, including boot.py at the root - so this is not
            # a missing-directory problem, it is the connection itself.
            print("""
         EVERY file failed, so this is the WebREPL connection, not the files.

         1. Wrong WebREPL password? The board's password is WEBREPL_PASSWORD in
            firmware/config/device_secrets.py. Pass it explicitly:
              python tools/deploy_wifi.py --host %s --password <pw>

         2. Is WebREPL actually running? The board's boot log must show
              [webrepl] started - wireless deploy available
            If not, re-flash over USB.

         3. Firewall: allow outbound TCP 8266 to the board.

         4. See the raw error:
              python tools/deploy_wifi.py --host %s -v
            or run one file by hand:
              python tools/webrepl_cli.py -p <pw> firmware/boot.py %s:/boot.py

         5. USB fallback (always works):
              mpremote fs cp -r firmware/. :
""" % (host, host, host))
        else:
            print("         If these are the config/ or lib/ files, the")
            print("         directories may not exist on the board yet -")
            print("         create them over USB, see docs/DEPLOY.md.")
        sys.exit(1)

    print("\n[deploy] done.")
    if args.reset:
        print("[deploy] soft-reset: open the WebREPL terminal and press Ctrl-D,")
        print("         or power-cycle the board.")
    print("[deploy] SAFETY: the board boots DISARMED. Verify relays are open")
    print("         before reconnecting anything to a person.")


if __name__ == "__main__":
    main()
