#!/usr/bin/env python3
"""One command to bring up the whole system: vision + control.

    python tools/launch.py --host 192.168.137.154
    python tools/launch.py --host 192.168.137.154 --no-board   # dry run
    python tools/launch.py --sim                               # no hardware at all

What it does:
  1. starts axon-main's pose service (its own uv environment) with the UDP
     feed enabled and pointed at our receiver
  2. waits until that service is actually up
  3. runs controller/run.py IN THE FOREGROUND, so the arrow keys work
  4. shuts the pose service down when you quit

WHY TWO PROCESSES AND NOT ONE ENVIRONMENT: axon-main needs Python 3.14 plus
mediapipe/opencv/fastapi via uv; our controller is deliberately standard-library
only. Merging them would pull a large vision stack into the safety-critical
control path and tie the two to one Python version. They stay separate; this
script just co-ordinates them.

SAFETY: this is the full system. Everything in docs/SAFETY.md applies. The
board still boots DISARMED and nothing stimulates until you press A.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_AXON = os.path.join(ROOT, "axon-main")


def wait_for_port(host, port, timeout_s, proc=None):
    """Wait until something accepts TCP on host:port. Returns True/False."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False                      # it died; don't keep waiting
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_pose_service(axon_dir, pose_host, pose_port, http_port, quiet):
    """Launch axon-main's uvicorn app with the UDP pose feed enabled."""
    env = dict(os.environ)
    env["POSE_UDP_ENABLED"] = "true"
    env["POSE_UDP_HOST"] = pose_host
    env["POSE_UDP_PORT"] = str(pose_port)
    # Belt and braces: their TENS driver must stay mocked. Our board is a
    # different protocol on a different port and would reject it anyway, but
    # two controllers on one relay board would defeat both sets of safety
    # rails, so we do not rely on that alone.
    env["DRIVER_MOCK_MODE"] = "true"

    cmd = ["uv", "run", "uvicorn", "backend.main:app",
           "--host", "127.0.0.1", "--port", str(http_port)]

    print("[launch] starting pose service in %s" % axon_dir)
    print("[launch]   %s" % " ".join(cmd))
    print("[launch]   POSE_UDP -> %s:%d   (their TENS driver forced to MOCK)"
          % (pose_host, pose_port))

    out = subprocess.DEVNULL if quiet else None
    try:
        return subprocess.Popen(cmd, cwd=axon_dir, env=env,
                                stdout=out, stderr=out)
    except FileNotFoundError:
        sys.exit("[launch] 'uv' not found. Install it, or start the pose "
                 "service yourself and re-run with --no-pose.")


def main():
    ap = argparse.ArgumentParser(description="Launch vision + control together")
    ap.add_argument("--host", help="board IP (passed to run.py)")
    ap.add_argument("--no-board", action="store_true",
                    help="real pose, but nothing is stimulated (do this first)")
    ap.add_argument("--sim", action="store_true",
                    help="simulated arm, no pose service, no board")
    ap.add_argument("--sim-hw", action="store_true",
                    help="simulated arm driving the REAL relays")
    ap.add_argument("--no-pose", action="store_true",
                    help="assume the pose service is already running")
    ap.add_argument("--axon", default=DEFAULT_AXON, help="path to axon-main")
    ap.add_argument("--pose-host", default="127.0.0.1",
                    help="where the pose service should SEND (this machine)")
    ap.add_argument("--pose-port", type=int, default=9090)
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--verbose", action="store_true",
                    help="show the pose service's own log output")
    args = ap.parse_args()

    need_pose = not (args.sim or args.sim_hw or args.no_pose)
    pose_proc = None

    try:
        if need_pose:
            if not os.path.isdir(args.axon):
                sys.exit("[launch] axon-main not found at %s (use --axon)"
                         % args.axon)
            pose_proc = start_pose_service(args.axon, args.pose_host,
                                           args.pose_port, args.http_port,
                                           quiet=not args.verbose)
            print("[launch] waiting for it to come up ...")
            if not wait_for_port("127.0.0.1", args.http_port, 90, pose_proc):
                if pose_proc.poll() is not None:
                    sys.exit("[launch] pose service exited immediately. Re-run "
                             "with --verbose to see why (first run also has to "
                             "download mediapipe, which is slow).")
                sys.exit("[launch] pose service did not start within 90s. "
                         "Re-run with --verbose.")
            print("[launch] pose service up on :%d" % args.http_port)
            print("[launch] NOTE: it only sends while it can SEE the arm - if "
                  "run.py shows pose:STALE, check the camera view.")

        # --- hand over to the controller, in the FOREGROUND ---------------
        # It must inherit this terminal or the arrow keys will not work.
        cmd = [sys.executable, os.path.join(ROOT, "controller", "run.py")]
        if args.sim:
            cmd.append("--sim")
        if args.sim_hw:
            cmd.append("--sim-hw")
        if args.no_board:
            cmd.append("--no-board")
        if args.host:
            cmd += ["--host", args.host]

        print("[launch] starting controller: %s\n" % " ".join(cmd[1:]))
        return subprocess.call(cmd, cwd=os.path.join(ROOT, "controller"))

    finally:
        if pose_proc is not None and pose_proc.poll() is None:
            print("\n[launch] stopping pose service ...")
            try:
                pose_proc.terminate()
                pose_proc.wait(timeout=8)
            except Exception:
                pose_proc.kill()
            print("[launch] stopped.")


if __name__ == "__main__":
    sys.exit(main() or 0)
