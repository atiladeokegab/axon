#!/usr/bin/env python3
"""One command to bring up the whole system: vision + control.

    python tools/launch.py --host 192.168.137.131
    python tools/launch.py --host 192.168.137.131 --no-board   # dry run
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


def kill_tree(proc, name):
    """Kill a child AND its descendants.

    `uv run uvicorn ...` starts uv, which starts uvicorn as a CHILD. Calling
    terminate() on the Popen object kills only uv, leaving uvicorn running -
    still holding the camera and port 8000, so the next launch fails and the
    webcam stays busy. Killing the whole tree is the only reliable cleanup.
    """
    if proc is None or proc.poll() is not None:
        return
    print("[launch] stopping %s ..." % name)
    try:
        if os.name == "nt":
            # /T = tree, /F = force. Windows has no process groups we can
            # signal the way POSIX does.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _popen_kwargs():
    """Start children so the whole tree can be cleaned up later."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def port_in_use(port, host="127.0.0.1"):
    """True if something is already listening there."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


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


MODEL_REL = os.path.join("models", "pose_landmarker_lite.task")


def ensure_axon_ready(axon_dir, skip_setup):
    """Make sure axon-main can actually start: deps synced, model downloaded.

    The MediaPipe model is gitignored and NOT in the repo, so a fresh clone
    fails at import time with an unhelpful error. Checking here turns that into
    a one-line explanation, and by default just fixes it.
    """
    model = os.path.join(axon_dir, MODEL_REL)
    have_model = os.path.exists(model)

    if have_model and skip_setup:
        return
    if have_model:
        return

    if skip_setup:
        sys.exit("[launch] missing %s\n"
                 "[launch] run:  cd %s && uv run python "
                 "scripts/download_pose_model.py" % (MODEL_REL, axon_dir))

    print("[launch] first-time setup for axon-main (this only happens once)")
    print("[launch]   uv sync   - downloads mediapipe/opencv, can take minutes")
    try:
        if subprocess.call(["uv", "sync"], cwd=axon_dir) != 0:
            sys.exit("[launch] 'uv sync' failed - run it by hand in %s" % axon_dir)
    except FileNotFoundError:
        sys.exit("[launch] 'uv' is not installed.\n"
                 "[launch] Install it: https://docs.astral.sh/uv/  then re-run.\n"
                 "[launch] (winget install astral-sh.uv)")

    print("[launch]   downloading the pose model (~5 MB)")
    if subprocess.call(["uv", "run", "python", "scripts/download_pose_model.py"],
                       cwd=axon_dir) != 0:
        sys.exit("[launch] model download failed - run it by hand in %s" % axon_dir)

    if not os.path.exists(model):
        sys.exit("[launch] model still missing at %s" % model)
    print("[launch] setup complete.\n")


def start_pose_service(axon_dir, pose_host, pose_port, http_port, quiet,
                       min_visibility=None):
    """Launch axon-main's uvicorn app with the UDP pose feed enabled."""
    env = dict(os.environ)
    env["POSE_UDP_ENABLED"] = "true"
    if min_visibility is not None:
        # Their estimator returns NO landmarks below this confidence, rather
        # than a low-confidence guess. Raising it trades tracking coverage for
        # correctness: dropped frames age out and stop stimulation, whereas a
        # confidently-wrong frame is acted upon. Use when pose_noise.py shows
        # bursts of mis-located samples. See docs/CONTROL.md.
        env["MIN_LANDMARK_VISIBILITY"] = str(min_visibility)
        print("[launch]   MIN_LANDMARK_VISIBILITY = %s (default 0.5)"
              % min_visibility)
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
                                stdout=out, stderr=out, **_popen_kwargs())
    except FileNotFoundError:
        sys.exit("[launch] 'uv' not found. Install it, or start the pose "
                 "service yourself and re-run with --no-pose.")


def open_in_browser(url):
    """Open url, preferring Chrome over whatever Windows calls the default.

    webbrowser.open() honours the OS default, which on a stock Windows box is
    Edge. The rest of the demo runs in Chrome, and the twin landing in a
    different browser means a second microphone permission prompt and a second
    place to look. Falls back to the default browser if Chrome is not found.
    """
    import shutil
    import webbrowser

    candidates = [
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                webbrowser.register(
                    "chrome", None, webbrowser.BackgroundBrowser(path))
                return webbrowser.get("chrome").open(url)
            except Exception:
                break  # fall through to the default browser
    return webbrowser.open(url)


def twin_page_url(frontend_port, http_port):
    """URL for twin.html, with the pose service wired in explicitly.

    twin.html is served by a dumb static server on frontend_port, but the
    camera stream and websocket live on the pose service at http_port. The
    page resolves "camera.mjpeg" relative to its OWN origin, so without this
    it asks the static server for a route that does not exist there, gets a
    404, and reports "Preview offline" while the camera is running perfectly
    well one port over. Passing ?ws= makes it derive both from http_port.
    """
    from urllib.parse import quote
    ws = quote("ws://127.0.0.1:%d/ws" % http_port, safe="")
    return "http://127.0.0.1:%d/twin.html?ws=%s" % (frontend_port, ws)


def start_frontend(axon_dir, port, quiet, http_port=None):
    """Serve axon-main/frontend so twin.html can be opened in a browser.

    It must be served over HTTP, not opened as a file: twin.html fetches the
    muscle meshes, and browsers block fetch() on file:// pages. It talks to the
    pose service directly over ws://127.0.0.1:8000/ws, so this is only a static
    file server.
    """
    fe = os.path.join(axon_dir, "frontend")
    if not os.path.isdir(fe):
        print("[launch] no frontend/ directory - skipping the 3D twin")
        return None
    if not os.path.exists(os.path.join(fe, "twin.html")):
        print("[launch] frontend/twin.html not found - skipping the 3D twin")
        return None

    # If the port is already taken (typically a leftover from a previous run),
    # http.server exits instantly. Say so instead of printing a URL that will
    # not load - the earlier version reported success either way.
    if port_in_use(port):
        print("[launch] PORT %d IS ALREADY IN USE - not starting the 3D twin." % port)
        print("[launch]   Most likely a leftover from a previous run:")
        print("[launch]     python tools/stop.py")
        print("[launch]   or pick another port:  --frontend-port 8082")
        return None

    out = subprocess.DEVNULL if quiet else None
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", fe],
        stdout=out, stderr=out, **_popen_kwargs())

    # Confirm it is actually serving before claiming it is.
    if not wait_for_port("127.0.0.1", port, 8, proc):
        print("[launch] the 3D twin server did not start (re-run with --verbose)")
        kill_tree(proc, "frontend server")
        return None

    # Printed WITH ?ws= so a copy-pasted URL works the same as the one opened
    # automatically. Without it the page 404s on camera.mjpeg against this
    # static server and reports the preview offline.
    if http_port is not None:
        print("[launch] 3D twin  ->  %s" % twin_page_url(port, http_port))
    else:
        print("[launch] 3D twin  ->  http://127.0.0.1:%d/twin.html" % port)
    return proc


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
    ap.add_argument("--pose-only", action="store_true",
                    help="start the pose service + 3D twin but NOT the "
                         "controller, so another tool (e.g. pose_noise.py) can "
                         "bind the pose port")
    ap.add_argument("--axon", default=DEFAULT_AXON, help="path to axon-main")
    ap.add_argument("--pose-host", default="127.0.0.1",
                    help="where the pose service should SEND (this machine)")
    ap.add_argument("--pose-port", type=int, default=9090)
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--verbose", action="store_true",
                    help="show the pose service's own log output")
    ap.add_argument("--skip-setup", action="store_true",
                    help="do not run uv sync / model download; fail if missing")
    ap.add_argument("--no-frontend", action="store_true",
                    help="do not serve the 3D twin page")
    ap.add_argument("--frontend-port", type=int, default=8081)
    ap.add_argument("--min-visibility", type=float,
                    help="raise axon-main's landmark confidence threshold "
                         "(default 0.5). Try 0.7 if pose_noise.py reports "
                         "bursts of mis-located samples.")
    ap.add_argument("--no-open", action="store_true",
                    help="do not open the 3D twin in a browser")
    ap.add_argument("--open", action="store_true",
                    help=argparse.SUPPRESS)   # now the default; kept for compat
    args = ap.parse_args()

    need_pose = not (args.sim or args.sim_hw or args.no_pose)
    pose_proc = None
    fe_proc = None

    try:
        if need_pose:
            if not os.path.isdir(args.axon):
                sys.exit("[launch] axon-main not found at %s (use --axon)"
                         % args.axon)
            ensure_axon_ready(args.axon, args.skip_setup)
            pose_proc = start_pose_service(args.axon, args.pose_host,
                                           args.pose_port, args.http_port,
                                           quiet=not args.verbose,
                                           min_visibility=args.min_visibility)
            print("[launch] waiting for it to come up ...")
            if not wait_for_port("127.0.0.1", args.http_port, 90, pose_proc):
                if pose_proc.poll() is not None:
                    sys.exit("[launch] pose service exited immediately. Re-run "
                             "with --verbose to see why (first run also has to "
                             "download mediapipe, which is slow).")
                sys.exit("[launch] pose service did not start within 90s. "
                         "Re-run with --verbose.")
            print("[launch] pose service up on :%d" % args.http_port)

            if not args.no_frontend:
                fe_proc = start_frontend(args.axon, args.frontend_port,
                                         quiet=not args.verbose,
                                         http_port=args.http_port)
            print("[launch] camera   ->  http://127.0.0.1:%d/camera.mjpeg"
                  % args.http_port)
            print("[launch] NOTE: pose is only sent while the arm is VISIBLE - "
                  "if run.py shows pose:STALE, check the camera view.")

            # Open the twin AUTOMATICALLY. It used to be opt-in (--open), which
            # meant the usual outcome was a printed URL nobody clicked and the
            # impression that the twin had not started at all. The twin is the
            # only thing you can actually watch, so show it by default.
            # Only after the server is confirmed serving, or the browser lands
            # on a dead port and shows a connection error.
            if fe_proc is not None and not args.no_open:
                twin_url = twin_page_url(args.frontend_port, args.http_port)
                print("[launch] opening the 3D twin in Chrome ...")
                if not open_in_browser(twin_url):
                    print("[launch] could not open a browser - go to %s"
                          % twin_url)

        # --- pose-only: hold here, leaving UDP 9090 free -------------------
        # run.py binds the pose port, so nothing else can receive poses while
        # it runs. This mode exists so tools/pose_noise.py (and anything else
        # that listens) can be used against a live feed.
        if args.pose_only:
            print("\n" + "=" * 62)
            print(" POSE ONLY - the controller is NOT running.")
            print("")
            if fe_proc is not None:
                print(" WATCH YOUR ARM HERE:  %s"
                      % twin_page_url(args.frontend_port, args.http_port))
            else:
                print(" (no 3D twin running - you have no view of the arm)")
            print("")
            print(" UDP %d is free. In a SECOND terminal:" % args.pose_port)
            print("     cd %s" % ROOT)
            print("     .venv\\Scripts\\activate")
            print("     py tools\\pose_noise.py")
            print("")
            print(" Leave THIS window open. Ctrl-C here when you are done.")
            print("=" * 62 + "\n")
            try:
                # A heartbeat, so an idle window is visibly alive rather than
                # looking like it has quietly died.
                n = 0
                while True:
                    if pose_proc is not None and pose_proc.poll() is not None:
                        print("[launch] pose service exited.")
                        break
                    time.sleep(0.5)
                    n += 1
                    if n % 60 == 0:            # every 30 s
                        print("[launch] still running (%d min) - Ctrl-C to stop"
                              % (n // 120))
            except KeyboardInterrupt:
                print("\n[launch] Ctrl-C - shutting down.")
            return 0

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
        print()
        kill_tree(fe_proc, "frontend server")
        kill_tree(pose_proc, "pose service")
        print("[launch] all stopped.")


if __name__ == "__main__":
    sys.exit(main() or 0)
