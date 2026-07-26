"""Human Control sub-app: page, websocket, and control endpoints."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from live_twin.backend.main import controller as live_actuation
from live_twin.backend.main import runtime as live_runtime

from .pad_driver import BoardPadDriver, pad_firing_enabled
from .service import ACTIONS, HumanControlService
from .session import ExternalSession

HC_ROOT = Path(__file__).resolve().parent
SITE_ROOT = HC_ROOT.parent
TEMPLATE_DIR = HC_ROOT / "templates"
SITE_TEMPLATE_DIR = SITE_ROOT / "templates"
SITE_STATIC_DIR = SITE_ROOT / "static"

templates = Jinja2Templates(directory=[TEMPLATE_DIR, SITE_TEMPLATE_DIR])

app = FastAPI(title="Axon Human Control")

# The shared header partial pulls the site stylesheets from /static, and this is
# a mounted sub-app with its own root, so it needs its own mount to reach them.
if SITE_STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=SITE_STATIC_DIR), name="hc-static")

# ONE service for the whole site. Not per-connection: two tabs must not be able
# to hold two different opinions about whether the system is armed.
service = HumanControlService(live_runtime)

# The external terminal session (tools/launch.py). It opens its OWN camera and
# its own pose service, so it and the in-browser loop are mutually exclusive -
# a webcam has one owner. Arbitrated in _claim_camera() below rather than left
# to the operator to remember.
session = ExternalSession()

# Give Live Twin's actuation controller a driver that reaches OUR board.
#
# It was built against a placeholder TCP protocol on port 5005 that nothing
# implements, hence the endless "[MOCK] would fire" lines. Swapping the driver
# rather than rewriting ActuationController keeps their validation, their
# arm/disarm state and their tests intact - the only thing that changes is what
# happens at the bottom of the stack.
#
# Injected HERE rather than inside live_twin, because human_control already
# imports live_twin; doing it the other way round would be a circular import.
# Still gated behind AXON_PAD_FIRING - the conversational agent can trigger
# these, and a voice command should not be able to stimulate someone unless
# that was switched on deliberately.
live_actuation.driver = BoardPadDriver(service=None)


async def _claim_camera(mode: str) -> str | None:
    """Give the camera to exactly one owner. Returns a note, or None.

    Called before starting either mode. Stopping the other one first is what
    prevents the failure this arbiter exists for: two systems half-working,
    each holding part of what the other needs, with nothing in either log
    saying so.
    """
    if mode == "web" and session.running:
        await session.stop()
        return "stopped the terminal session - it was holding the camera"
    if mode == "external" and service.status().get("running"):
        await service.stop()
        return "stopped the in-browser loop - it was holding the camera"
    return None


# The pad driver needs to know whether the control loop is using the board, so
# the two never write to it at once. Wired after `service` exists.
live_actuation.driver._service = service


def _camera_owner() -> str:
    if session.running:
        return "external"
    if service.status().get("running"):
        return "browser"
    return "none"


@app.get("/")
async def human_control_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Commands in, status out.

    Every reply carries the FULL status rather than a delta, so a browser that
    misses a frame cannot end up displaying a stale armed/disarmed state. On a
    control surface that is worth the extra bytes.
    """
    await websocket.accept()
    queue = service.subscribe()
    pump = None
    poller = None
    try:
        await websocket.send_text(json.dumps({
            "type": "status",
            "status": service.status(),
            "session": session.status(),
            "camera_owner": _camera_owner(),
            "pad_firing": pad_firing_enabled(),
            "actions": sorted(ACTIONS),
        }))
        pump = asyncio.create_task(_forward_status(websocket, queue))
        poller = asyncio.create_task(_poll_session(websocket))
        await _handle_commands(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        service.unsubscribe(queue)
        for task in (pump, poller):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        # DELIBERATELY does not stop the service. A closed tab must not become
        # an uncommanded release: silence would trip the board watchdog and drop
        # the arm mid-movement. See the note in service.py.


async def _forward_status(websocket: WebSocket, queue: asyncio.Queue):
    while True:
        payload = await queue.get()
        await websocket.send_text(json.dumps({
            "type": "status", "status": payload,
            "session": session.status(), "camera_owner": _camera_owner(),
        }))


async def _poll_session(websocket: WebSocket):
    """Push the external session's output even when the control loop is idle.

    Without this the page would only update while the in-browser loop is
    ticking - so in external mode, the mode where the browser is purely a
    monitor, nothing would ever appear.
    """
    while True:
        await asyncio.sleep(0.5)
        await websocket.send_text(json.dumps({
            "type": "status", "status": service.status(),
            "session": session.status(), "camera_owner": _camera_owner(),
        }))


async def _handle_commands(websocket: WebSocket):
    while True:
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        cmd = msg.get("cmd")
        note = None

        if cmd == "launch":
            # External terminal session: launch.py, with its own console so the
            # arrow keys land somewhere that can read them.
            try:
                note = await _claim_camera("external")
                cmdline = await session.start(host=msg.get("host") or None)
                note = ((note + "; ") if note else "") + "started: " + cmdline
            except Exception as exc:
                note = "could not launch: %r" % (exc,)
        elif cmd == "launch_stop":
            await session.stop()
            note = "terminal session stopped - camera released"
        elif cmd == "start":
            try:
                note = await _claim_camera("web")
                await service.start(board_host=msg.get("host") or None,
                                    simulated=bool(msg.get("simulated", True)))
                started = ("connected in DRY RUN - nothing is stimulated"
                           if msg.get("simulated", True)
                           else "connected to the board - relays will fire")
                note = ((note + "; ") if note else "") + started
            except Exception as exc:
                note = "could not start: %r" % (exc,)
        elif cmd == "stop":
            await service.stop()
            note = "stopped"
        elif cmd == "arm":
            note = service.arm()
        elif cmd == "disarm":
            note = service.disarm()
        elif cmd == "kill":
            note = service.kill()
        elif cmd == "grip":
            note = service.set_grip(msg.get("closed", True))
        elif cmd == "jog":
            note = service.jog(msg.get("action"), msg.get("steps", 1))
        elif cmd == "status":
            pass
        else:
            note = "unknown command %r" % (cmd,)

        await websocket.send_text(json.dumps({
            "type": "status", "status": service.status(),
            "session": session.status(), "camera_owner": _camera_owner(),
            "note": note,
        }))
