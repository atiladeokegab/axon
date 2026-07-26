"""
FastAPI app: streams live pose landmarks to the frontend over WebSocket,
and fires TENS pads (through ActuationController — never TensClient
directly) when the frontend reports a selected motion.

WebSocket contract (my proposed default — confirm with whoever's doing
frontend, then delete this comment):
    Server -> client:
        {"type": "ready", "armed": bool}
            sent once on connect. armed=false means every fire is mocked
            (logged, not sent to real hardware) regardless of what the
            client asks for — the UI should show this state unmistakably.
        {"type": "pose",
         "landmarks": {"shoulder": [x,y,z], "elbow": [x,y,z], "wrist": [x,y,z]} | null,
         "side": "left" | "right" | null,
         "status": "tracking" | "no_person" | "arm_not_visible"}
            sent every frame, including frames with no landmarks — a client
            that stops receiving pose messages should treat that as the
            stream being down, not as "arm out of frame". status says which
            of those two it is so the UI can prompt the user specifically.
        {"type": "status", "pad": "BICEP", "state": "firing", "duration_ms": 800}
        {"type": "status", "pad": "BICEP", "state": "done" | "error", "detail": "..."}
        {"type": "side", "side": "left" | "right" | null}
            ack of a set_side request; null means auto-pick.
    Client -> server:
        {"type": "select_motion", "motion": "grip" | "raise" | "lower" | "push_forward" | "pull_back"}
        {"type": "set_side", "side": "left" | "right" | null}
            forces which arm is tracked. A forced side never falls back to
            the other arm — see estimator.ArmPoseEstimator.

Note the side is broadcaster-wide, not per-connection: there is one camera
and one estimator, so the last client to set it wins for everyone.
"""
import asyncio
import contextlib
import json
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import httpx
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from live_twin.backend import config
from live_twin.backend.actuation.controller import ActuationController, ActuationError
from live_twin.backend.pose.broadcaster import PoseBroadcaster
from live_twin.backend.pose.model_asset import ensure_pose_model
from live_twin.backend.placement.pipeline import PlacementError, compute_placement

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

broadcaster = PoseBroadcaster()
controller = ActuationController()

LIVE_TWIN_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = LIVE_TWIN_ROOT / "frontend"
SITE_TEMPLATE_DIR = LIVE_TWIN_ROOT.parent / "templates"
templates = Jinja2Templates(directory=[FRONTEND_DIR, SITE_TEMPLATE_DIR])


class LiveRuntime:
    """Own the shared camera/model while at least one live client needs it."""

    def __init__(
        self,
        pose_broadcaster: PoseBroadcaster,
        actuation_controller: ActuationController,
    ):
        self.broadcaster = pose_broadcaster
        self.controller = actuation_controller
        self._active_clients = 0
        self._lock = asyncio.Lock()

    @property
    def active_clients(self) -> int:
        return self._active_clients

    async def acquire(self) -> None:
        async with self._lock:
            if self._active_clients == 0:
                try:
                    await self.broadcaster.start()
                except Exception:
                    await self.broadcaster.stop()
                    raise
            self._active_clients += 1

    async def release(self) -> None:
        async with self._lock:
            if self._active_clients == 0:
                return
            self._active_clients -= 1
            if self._active_clients == 0:
                await self.controller.stop()
                await self.broadcaster.stop()

    async def shutdown(self) -> None:
        async with self._lock:
            self._active_clients = 0
            await self.controller.stop()
            await self.broadcaster.stop()


runtime = LiveRuntime(broadcaster, controller)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Fetch the pose model now rather than on the first connection. A missing
    # model previously surfaced as a 503 the moment someone opened the tab,
    # which is the worst possible time to discover it.
    try:
        ensure_pose_model()
    except FileNotFoundError as exc:
        logger.error("%s", exc)
    try:
        yield
    finally:
        await runtime.shutdown()


app = FastAPI(title="Axon Live Twin", lifespan=_lifespan)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.get("/")
async def live_twin_home(request: Request):
    """Render Live Twin beneath the shared Axon site header."""
    return templates.TemplateResponse(request=request, name="twin.html")


async def _mjpeg_stream():
    try:
        async for jpeg in broadcaster.frames():
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                + jpeg
                + b"\r\n"
            )
    finally:
        await runtime.release()


@app.get("/camera.mjpeg")
async def camera_stream_endpoint():
    """Preview the broadcaster's camera without opening a second device handle."""
    try:
        await runtime.acquire()
    except Exception as exc:
        logger.exception("Live camera runtime could not start.")
        raise HTTPException(
            status_code=503,
            detail="The live camera or pose model could not be started.",
        ) from exc
    return StreamingResponse(
        _mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _forward_pose(websocket: WebSocket, queue: asyncio.Queue):
    while True:
        payload = await queue.get()
        await websocket.send_text(json.dumps(payload))


async def _handle_client_messages(websocket: WebSocket):
    while True:
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if msg.get("type") == "set_side":
            side = msg.get("side")
            try:
                broadcaster.set_side(side)
            except ValueError as exc:
                await websocket.send_text(json.dumps({
                    "type": "status", "pad": None, "state": "error", "detail": str(exc),
                }))
                continue
            logger.info("tracked arm set to %s", side or "auto")
            await websocket.send_text(json.dumps({"type": "side", "side": side}))
            continue

        if msg.get("type") != "select_motion":
            continue

        motion = msg.get("motion")
        pad = config.MOTION_TO_PAD.get(motion)
        if pad is None:
            await websocket.send_text(json.dumps({
                "type": "status", "pad": None, "state": "error",
                "detail": f"unknown motion '{motion}'",
            }))
            continue

        await websocket.send_text(json.dumps({
            "type": "status", "pad": pad, "state": "firing",
            "duration_ms": config.DEFAULT_DURATION_MS,
        }))
        try:
            await controller.fire(pad)
            await websocket.send_text(json.dumps({"type": "status", "pad": pad, "state": "done"}))
        except ActuationError as exc:
            logger.warning("actuation rejected for pad %s: %s", pad, exc)
            await websocket.send_text(json.dumps({
                "type": "status", "pad": pad, "state": "error", "detail": str(exc),
            }))


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = None
    forward_task = None
    try:
        await runtime.acquire()
        await websocket.send_text(json.dumps({"type": "ready", "armed": controller.armed}))
        queue = broadcaster.subscribe()
        forward_task = asyncio.create_task(_forward_pose(websocket, queue))
        await _handle_client_messages(websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Live WebSocket runtime could not start.")
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({
                "type": "status",
                "pad": None,
                "state": "error",
                "detail": "The live camera or pose model could not be started.",
            }))
            await websocket.close(code=1011)
    finally:
        if queue is not None:
            broadcaster.unsubscribe(queue)
        if forward_task is not None:
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await forward_task
        await runtime.release()


@app.get("/agent/diagnose")
async def agent_diagnose():
    """Work out WHICH auth problem the agent has, rather than guessing.

    A 401 from get-signed-url has several quite different causes needing
    different fixes: a wrong key, a valid key missing a permission scope, or an
    agent in another workspace. From outside they look identical.

    CRITICAL DISTINCTION, learned the hard way. ElevenLabs answers an
    unscoped-but-valid key with 401 and status "missing_permissions". An
    earlier version of this probe read that as "the key is rejected" and sent
    someone hunting for a new key when the one they had was fine. A missing
    permission means AUTHENTICATED BUT NOT AUTHORISED - it proves the key is
    real - so it is reported as such and never aborts the chain.

    The /v1/user probe is informational only for the same reason: it needs the
    user_read scope, which this integration does not require. Failing it says
    nothing about whether the agent will work.

    The key is NEVER returned, only its length and first/last four characters.
    """
    agent_id = os.environ.get("AXON_AGENT_ID", "").strip()
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

    report = {
        "agent_id": agent_id or None,
        "key_loaded": bool(api_key),
        "key_length": len(api_key),
        "key_fingerprint": (api_key[:4] + "\u2026" + api_key[-4:]) if len(api_key) > 8 else None,
        "steps": [],
        "verdict": None,
    }
    if not api_key:
        report["verdict"] = "No ELEVENLABS_API_KEY is loaded. Check webapp_demo/.env."
        return report
    if not agent_id:
        report["verdict"] = "No AXON_AGENT_ID is loaded. Check webapp_demo/.env."
        return report

    headers = {"xi-api-key": api_key}
    checks = [
        ("user_read (not required - informational)",
         "https://api.elevenlabs.io/v1/user", None, False),
        ("can see this agent",
         f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}", None, True),
        ("can sign a session",
         "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
         {"agent_id": agent_id}, True),
    ]

    def _classify(response):
        """Separate 'not authorised for this' from 'not a valid key'."""
        try:
            body = response.json().get("detail", {})
        except Exception:
            body = {}
        status = body.get("status") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else None
        return status, message

    async with httpx.AsyncClient(timeout=10.0) as client:
        for label, url, params, required in checks:
            try:
                r = await client.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                report["steps"].append({"check": label, "ok": False,
                                        "required": required,
                                        "detail": f"network error: {exc}"})
                break
            status, message = _classify(r)
            step = {
                "check": label,
                "status": r.status_code,
                "ok": r.status_code == 200,
                "required": required,
                "missing_permission": status == "missing_permissions",
            }
            if r.status_code != 200:
                step["detail"] = message or r.text[:180]
            report["steps"].append(step)
            # Deliberately does NOT break: every step is informative, and an
            # optional one failing must not hide the answer to the real question.

    signing = next((s for s in report["steps"] if s["check"] == "can sign a session"), None)
    seeing = next((s for s in report["steps"] if s["check"] == "can see this agent"), None)
    any_auth_proof = any(s.get("missing_permission") or s.get("ok")
                         for s in report["steps"])

    if signing and signing["ok"]:
        report["verdict"] = "Signing works. The agent should connect."
    elif not any_auth_proof:
        report["verdict"] = (
            "Every call was rejected outright, with no permission message - so "
            "the key itself is not recognised. Check key_fingerprint against "
            "the key you believe is correct."
        )
    elif signing and signing.get("missing_permission"):
        report["verdict"] = (
            "THE KEY IS VALID but lacks the Conversational AI permission. In "
            "the ElevenLabs dashboard, edit this API key and enable the "
            "Conversational AI scope (user_read is NOT needed). Nothing to "
            "change in the code."
        )
    elif seeing and seeing["status"] == 404:
        report["verdict"] = (
            "The key is valid but this agent does not exist under it - the "
            "agent most likely belongs to a different workspace or account."
        )
    else:
        report["verdict"] = (
            "The key authenticates but signing was refused. Check the key's "
            "Conversational AI scope first, then the plan's convai quota."
        )
    return report


@app.get("/agent/session")
async def agent_session():
    """Mint a signed URL for the ElevenLabs conversational agent.

    WHY THE BROWSER CANNOT JUST USE THE AGENT ID. A bare agentId only works for
    an agent explicitly marked public. For anything else ElevenLabs requires a
    short-lived signed URL, minted server-side with the API key - and the key
    must never reach the browser, so this has to be an endpoint.

    Without it the session appears to start and then produces no audio, which
    is exactly the symptom "we could speak to it but heard nothing back": the
    microphone opens locally, so the UI looks connected, while the socket was
    never authorised.

    Returns the agent id alone if no key is configured, so a genuinely public
    agent still works and the page can fall back rather than break.
    """
    agent_id = os.environ.get("AXON_AGENT_ID", "").strip()
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

    if not agent_id:
        raise HTTPException(
            status_code=503,
            detail="AXON_AGENT_ID is not configured on the server.",
        )
    if not api_key:
        # Public-agent path. Not an error: say so plainly so the page can decide.
        return {"agent_id": agent_id, "signed_url": None, "mode": "public"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": agent_id},
                headers={"xi-api-key": api_key},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not reach ElevenLabs: {exc}",
        ) from exc

    if response.status_code != 200:
        # Surface their message rather than a generic failure - it is usually
        # specific ("agent not found", "quota exceeded") and worth reading.
        raise HTTPException(
            status_code=503,
            detail=("ElevenLabs refused to sign the session "
                    f"({response.status_code}): {response.text[:200]}"),
        )

    signed = response.json().get("signed_url")
    if not signed:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs returned no signed_url.",
        )
    return {"agent_id": agent_id, "signed_url": signed, "mode": "signed"}


@app.post("/placement")
async def placement_endpoint(
    relaxed: UploadFile = File(...),
    bicep_flexed: UploadFile = File(...),
    tricep_flexed: UploadFile = File(...),
    front: UploadFile = File(...),
    back: UploadFile = File(...),
):
    """
    5-photo pad-placement calibration. See placement/pipeline.py for the
    exact protocol each file needs to follow. Always returns 200 with a
    per-pad {"ok", "point", "detail", "overlay_b64"} breakdown plus an
    overall "calibration_complete" flag — a partial/failed calibration is
    visible in the response rather than an all-or-nothing error. 422 is
    reserved for malformed input (an upload that isn't a decodable image).
    """
    try:
        return compute_placement(
            relaxed=await relaxed.read(),
            bicep_flexed=await bicep_flexed.read(),
            tricep_flexed=await tricep_flexed.read(),
            front=await front.read(),
            back=await back.read(),
        )
    except PlacementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
