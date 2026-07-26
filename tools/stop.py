#!/usr/bin/env python3
"""Stop anything left running from a previous launch.

    python tools/stop.py

Closing the browser does NOT stop the system - the browser is only a viewer.
The real processes are the pose service (uvicorn, holds the webcam and port
8000) and the static file server for the 3D twin (port 8081).

Normally `tools/launch.py` cleans both up when you quit with Q. Use this if the
launcher's window was closed abruptly, or if a run predates the tree-kill fix
and left an orphaned uvicorn holding the camera.

Symptoms that mean you need this:
  * "camera in use" / a black preview
  * "address already in use" on 8000 or 8081
  * the camera LED still on after you thought you had stopped
"""

import argparse
import os
import socket
import subprocess
import sys

PORTS = [
    (8000, "pose service (uvicorn) - also holds the webcam"),
    (8081, "3D twin static server"),
    (9090, "pose UDP receiver (our controller)"),
]


def pids_on_port(port):
    """PIDs listening on a TCP port. Windows netstat / POSIX lsof."""
    pids = set()
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == "TCP":
                    local = parts[1]
                    if local.endswith(":%d" % port) and parts[3] == "LISTENING":
                        pids.add(parts[4])
        else:
            out = subprocess.run(["lsof", "-ti", "tcp:%d" % port],
                                 capture_output=True, text=True).stdout
            pids.update(p for p in out.split() if p.strip())
    except FileNotFoundError:
        pass
    return sorted(pids)


def port_busy(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def kill(pid):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["kill", "-9", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="Stop leftover launch processes")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="show what would be killed, kill nothing")
    args = ap.parse_args()

    found_any = False
    for port, what in PORTS:
        busy = port_busy(port)
        pids = pids_on_port(port)
        if not busy and not pids:
            print("  :%-5d free        %s" % (port, what))
            continue
        found_any = True
        if not pids:
            print("  :%-5d IN USE      %s  (could not identify the PID)"
                  % (port, what))
            continue
        for pid in pids:
            if args.dry_run:
                print("  :%-5d would kill PID %-7s %s" % (port, pid, what))
            else:
                ok = kill(pid)
                print("  :%-5d %s PID %-7s %s"
                      % (port, "killed " if ok else "FAILED ", pid, what))

    if not found_any:
        print("\nNothing was running.")
    elif args.dry_run:
        print("\nDry run - nothing killed. Re-run without -n to stop them.")
    else:
        print("\nDone. If the webcam is still busy, the holder may be another "
              "app (Teams/Zoom/Camera).")


if __name__ == "__main__":
    sys.exit(main())
