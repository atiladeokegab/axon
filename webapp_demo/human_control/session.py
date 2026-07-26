"""Supervise the external terminal session (tools/launch.py).

WHY THIS EXISTS RATHER THAN JUST DOCUMENTING A COMMAND. There are two ways to
drive the arm and they cannot run at once:

  * IN-BROWSER   - the loop inside FastAPI, reading pose from the LiveRuntime
                   this app already owns.
  * EXTERNAL     - tools/launch.py, which starts its own pose service and its
                   own copy of the twin, and opens its own camera.

Both want the webcam, and a webcam has exactly one owner. Worse, launch.py's
pose service defaults to HTTP port 8000 - the port this site usually runs on -
so starting it blind produces a port clash on top of a camera clash. Left to
documentation, the failure mode is two half-working systems and no obvious
cause. So ownership is arbitrated in code: starting one stops the other.

The external session keeps its own terminal, because that is where the arrow
keys work; a browser cannot forward keystrokes into another process's stdin.
What the browser gets instead is the session's OUTPUT, streamed live, so the
status line and any fault it reports are visible on the page without alt-tabbing.
"""

import asyncio
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SITE_ROOT.parent
LAUNCH_PY = REPO_ROOT / "tools" / "launch.py"

# Enough scrollback to see a startup failure and the last status lines, without
# letting a long session grow unboundedly in memory.
LOG_LINES = 400

# launch.py's own pose service and twin. The HTTP port must not be 8000: that is
# the site's port, and a clash there fails in a way that looks like success.
POSE_HTTP_PORT = 8010
TWIN_PORT = 8081


class ExternalSession:
    """Runs tools/launch.py and streams its output."""

    def __init__(self):
        self._proc = None
        self._reader = None
        self.log = deque(maxlen=LOG_LINES)
        self.command = None
        self.exit_code = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---- lifecycle --------------------------------------------------------
    async def start(self, host=None, extra_args=None, own_terminal=True):
        """Spawn launch.py. Returns the command line actually used."""
        if self.running:
            raise RuntimeError("a session is already running")
        if not LAUNCH_PY.is_file():
            raise FileNotFoundError(
                "Cannot find %s. The site must be run from inside the "
                "repository." % LAUNCH_PY
            )

        cmd = [sys.executable, str(LAUNCH_PY)]
        if host:
            cmd += ["--host", str(host)]

        # MOVE THE POSE SERVICE OFF PORT 8000. launch.py defaults to 8000, which
        # is this site's own port - and its readiness check only asks whether
        # SOMETHING is listening there. Started underneath the webapp it would
        # therefore report success because it found US, then point its twin at
        # ws://127.0.0.1:8000/ws, which is this site's root and not its pose
        # service. It would look like it started and quietly never track.
        if not any(a.startswith("--http-port") for a in (extra_args or [])):
            cmd += ["--http-port", str(POSE_HTTP_PORT)]
        if not any(a.startswith("--frontend-port") for a in (extra_args or [])):
            cmd += ["--frontend-port", str(TWIN_PORT)]

        cmd += list(extra_args or [])

        self.log.clear()
        self.exit_code = None
        self.command = " ".join(cmd)
        self._append("$ " + self.command)

        creationflags = 0
        popen_kwargs = {}
        if own_terminal and os.name == "nt":
            # A REAL console window, because that is where the arrow keys have
            # to go. run.py reads the keyboard with msvcrt from its own console;
            # piped into this process it would have no console to read from and
            # every keypress would be lost.
            creationflags = subprocess.CREATE_NEW_CONSOLE
            popen_kwargs["creationflags"] = creationflags
            self._append("[session] opened in a new console window - "
                         "click it and use the keyboard there")
        else:
            # No console available (or not Windows): capture the output instead
            # so at least the status line is visible on the page.
            popen_kwargs.update(
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            if os.name != "nt":
                popen_kwargs["start_new_session"] = True

        self._proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), **popen_kwargs)

        if self._proc.stdout is not None:
            self._reader = asyncio.create_task(self._pump())
        return self.command

    async def stop(self):
        if not self.running:
            return
        self._append("[session] stopping ...")
        try:
            if os.name == "nt":
                # /T kills the tree. launch.py starts children of its own
                # (uv -> uvicorn), and terminating only the parent would orphan
                # them still holding the camera and the port.
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import signal
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass
        try:
            self._proc.wait(timeout=8)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self.exit_code = self._proc.poll()
        self._append("[session] stopped (exit %s)" % self.exit_code)
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        self._proc = None

    # ---- output -----------------------------------------------------------
    async def _pump(self):
        loop = asyncio.get_running_loop()
        stream = self._proc.stdout
        while True:
            line = await loop.run_in_executor(None, stream.readline)
            if not line:
                break
            self._append(line.rstrip("\n"))
        self.exit_code = self._proc.poll() if self._proc else None

    def _append(self, line):
        self.log.append(line)

    def status(self):
        return {
            "running": self.running,
            "command": self.command,
            "exit_code": self.exit_code,
            "log": list(self.log)[-60:],
            "captures_output": self._proc is not None and self._proc.stdout is not None,
        }
