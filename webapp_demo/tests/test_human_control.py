"""Human Control tab: the parts that must not silently drift.

These deliberately avoid needing a camera or a board. What they protect is the
integration itself - that the web tab and the terminal client are still running
the same control code, with the same axis mapping, and that the safety posture
survives a refactor.
"""

import ast
import re
from pathlib import Path

import pytest

SITE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SITE_ROOT.parent
HC = SITE_ROOT / "human_control"


def _actions():
    tree = ast.parse((HC / "service.py").read_text(encoding="utf-8"))
    return next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "ACTIONS"
    )


def test_control_code_is_imported_not_copied():
    """The single most important property of this integration.

    If the control loop is ever vendored into the site, the two entry points can
    drift, and a divergence there is a divergence in how a person's arm is
    driven. The bridge must point at the real controller package.
    """
    assert (REPO_ROOT / "controller" / "control_loop.py").exists()
    for name in ("pid.py", "mapping.py", "kinematics.py", "filters.py"):
        assert not (HC / name).exists(), (
            f"{name} has been copied into human_control/. Import it from "
            "controller/ instead - two copies WILL diverge."
        )






def test_closing_a_browser_tab_does_not_stop_the_loop():
    """Silence would trip the board watchdog and DROP the arm mid-movement.

    The websocket teardown must not call service.stop(); only an explicit stop,
    or app shutdown, may end the session.
    """
    src = (HC / "app.py").read_text(encoding="utf-8")
    ws_body = src[src.index("async def ws_endpoint"):src.index("async def _forward_status")]
    assert "service.stop()" not in ws_body, (
        "the websocket handler must not stop the control loop on disconnect"
    )


def test_shutdown_disarms_before_the_camera_goes_away():
    """On app shutdown the board should hear an explicit disarm, not silence."""
    src = (SITE_ROOT / "ems_muscle_mapper" / "main.py").read_text(encoding="utf-8")
    assert "human_control_service.stop()" in src
    assert src.index("human_control_service.stop()") < src.index(
        "live_twin_runtime.shutdown()"
    ), "the control loop must be stopped before the runtime it reads from"


def test_the_tab_is_mounted_and_linked():
    main = (SITE_ROOT / "ems_muscle_mapper" / "main.py").read_text(encoding="utf-8")
    assert 'app.mount("/human-control"' in main
    header = (SITE_ROOT / "templates" / "partials" / "site_header.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/human-control/"' in header
    assert "product-tab--placeholder" not in header, (
        "the placeholder tab should have been replaced, not left alongside"
    )


def test_pose_bridge_does_not_reimplement_the_filtering():
    """It must push through the real PoseReceiver, not roll its own.

    The receiver carries the rate gate, median window, one-euro filter and
    freeze detection that were tuned against real captures. A parallel
    implementation here would guarantee the two paths diverge.
    """
    src = (HC / "pose_bridge.py").read_text(encoding="utf-8")
    assert "from pose_api import PoseReceiver" in src
    assert "_rx._ingest" in src
    for invented in ("median", "one_euro", "OneEuro"):
        assert f"def {invented}" not in src, "filtering must not be reimplemented here"


def test_only_one_owner_can_hold_the_camera():
    """The in-browser loop and the terminal session both open the webcam.

    A webcam has one owner, and launch.py's pose service also defaults to HTTP
    port 8000 - the site's own port. Starting one without stopping the other
    gives two half-working systems with nothing in either log explaining why,
    so the arbitration must be in code rather than in a README.
    """
    src = (HC / "app.py").read_text(encoding="utf-8")
    assert "_claim_camera" in src
    body = src[src.index("async def _claim_camera"):src.index("def _camera_owner")]
    assert "session.stop()" in body, "claiming for the browser must stop the terminal session"
    assert "service.stop()" in body, "claiming for the terminal must stop the browser loop"

    # ...and both entry points must actually go through it.
    handler = src[src.index("async def _handle_commands"):]
    for cmd in ('if cmd == "launch"', 'elif cmd == "start"'):
        block = handler[handler.index(cmd):]
        assert "_claim_camera" in block[:400], f"{cmd} does not claim the camera"


def test_terminal_session_gets_a_real_console():
    """run.py reads the keyboard with msvcrt from its own console.

    Piped into the web process it would have no console to read from, so every
    arrow key would be silently lost - the session would look alive and ignore
    you.
    """
    src = (HC / "session.py").read_text(encoding="utf-8")
    assert "CREATE_NEW_CONSOLE" in src


def test_terminal_session_is_killed_as_a_tree():
    """launch.py spawns uv, which spawns uvicorn. Killing only the parent
    orphans a child still holding the camera and the port."""
    src = (HC / "session.py").read_text(encoding="utf-8")
    assert '"/T"' in src or "'/T'" in src





def test_board_host_reaches_the_launcher():
    """Without --host, launch.py falls back to UDP auto-discovery, which fails
    as a timeout behind a firewall - and a timeout reads as a dead board."""
    page = (HC / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'cmd: "launch", host: host || null' in page
    assert 'id="host"' in page, "there must be somewhere to type the board IP"

    src = (HC / "session.py").read_text(encoding="utf-8")
    assert '"--host"' in src, "session.py must pass the host through to launch.py"


def test_every_page_has_the_favicon():
    pages = [
        SITE_ROOT / "index.html",
        HC / "templates" / "index.html",
        SITE_ROOT / "live_twin" / "frontend" / "twin.html",
        SITE_ROOT / "ems_muscle_mapper" / "templates" / "index.html",
    ]
    for page in pages:
        assert 'rel="icon"' in page.read_text(encoding="utf-8"), f"{page.name} has no favicon"


def test_wordmark_glyph_is_hidden_from_assistive_tech():
    """The neuron replaces a letter. Read aloud it would be noise, and the
    anchor already carries an accessible name."""
    header = (SITE_ROOT / "templates" / "partials" / "site_header.html").read_text(
        encoding="utf-8"
    )
    assert 'aria-label="Axon home"' in header
    assert 'class="site-header__wordmark" aria-hidden="true"' in header
    assert 'alt=""' in header


def test_pad_firing_reaches_our_board_not_the_placeholder_protocol():
    """Live Twin shipped against a TCP driver on port 5005 that nothing
    implements - hence the endless "[MOCK] would fire". The adapter must speak
    OUR protocol: a UDP duty vector via the controller's link."""
    src = (HC / "pad_driver.py").read_text(encoding="utf-8")
    assert "from link import EspLink" in src
    assert "send_duties" in src
    assert "PAD_TO_CHANNEL" in src

    app_src = (HC / "app.py").read_text(encoding="utf-8")
    assert "live_actuation.driver = BoardPadDriver" in app_src, (
        "the driver must be injected, or ActuationController keeps its mock"
    )


def test_every_pad_maps_to_a_real_channel():
    """A pad with no channel would fail at firing time, in front of a subject."""
    import ast as _ast
    src = (HC / "pad_driver.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    mapping_ = next(
        _ast.literal_eval(n.value) for n in _ast.walk(tree)
        if isinstance(n, _ast.Assign)
        and getattr(n.targets[0], "id", "") == "PAD_TO_CHANNEL"
    )
    cfg = (SITE_ROOT / "live_twin" / "backend" / "config.py").read_text(encoding="utf-8")
    pads = set(re.findall(r'^PAD_\w+ = "(\w+)"', cfg, re.M))
    assert pads, "could not find the pad names in live_twin config"
    missing = pads - set(mapping_)
    assert not missing, f"pads with no channel wired: {sorted(missing)}"

    valid = {"CH%d" % i for i in range(1, 9)}
    assert set(mapping_.values()) <= valid, "mapped to a channel that does not exist"


def test_pad_firing_is_off_unless_explicitly_enabled():
    """The conversational agent can trigger pad firings. A voice command must
    not be able to stimulate someone without a deliberate opt-in."""
    src = (HC / "pad_driver.py").read_text(encoding="utf-8")
    assert "AXON_PAD_FIRING" in src
    assert 'os.environ.get("AXON_PAD_FIRING", "")' in src

    env = (SITE_ROOT / ".env")
    if env.exists():
        assert "AXON_PAD_FIRING=0" in env.read_text(encoding="utf-8"), (
            "the shipped default must be off"
        )


def test_pad_firing_refuses_while_the_control_loop_owns_the_board():
    """Both write duty vectors. Together they would overwrite each other 30
    times a second and the arm would judder between two intentions."""
    src = (HC / "pad_driver.py").read_text(encoding="utf-8")
    assert "_refuse_if_busy" in src
    body = src[src.index("def _refuse_if_busy"):src.index("def _ensure_link")]
    assert "PadFiringRefused" in body


def test_pad_firing_always_releases_the_board():
    """Relying on the watchdog would work but takes up to 500 ms and leaves the
    board armed - a worse resting state than an explicit stop."""
    src = (HC / "pad_driver.py").read_text(encoding="utf-8")
    hold = src[src.index("async def _hold"):src.index("def stop")]
    assert "finally:" in hold
    assert "disarm()" in hold

def test_the_page_is_a_launcher_not_a_control_surface():
    """The arrow keys must reach the session's console.

    An earlier version put a keydown handler and jog buttons on this page. With
    the browser focused that swallowed every press the operator meant for the
    terminal, and a second control loop in the tab would also fight the real one
    for the board. The page starts and stops the session; the session drives.
    """
    page = (HC / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'addEventListener("keydown"' not in page, "the page must not capture keys"
    assert "data-jog" not in page, "jogging belongs to the session, not the page"
    assert 'cmd: "arm"' not in page and 'cmd: "kill"' not in page


def test_the_page_does_not_embed_the_sites_own_twin():
    """launch.py brings its own twin. Framing THIS site's Live Twin instead
    started a second camera client and pulled in the ElevenLabs agent, neither
    of which belongs in a control session."""
    page = (HC / "templates" / "index.html").read_text(encoding="utf-8")
    assert "/live-twin/" not in page
    assert "iframe" not in page
    assert "8081/twin.html" in page, "it should link to the session's own twin"


def test_launcher_moves_the_pose_service_off_the_sites_port():
    """launch.py defaults its pose service to 8000 - this site's port - and its
    readiness check only asks whether SOMETHING is listening there. Started
    underneath the webapp it would find US, report success, and then point its
    twin at the wrong server. It has to be given a different port."""
    src = (HC / "session.py").read_text(encoding="utf-8")
    assert "POSE_HTTP_PORT" in src
    assert "8010" in src, "the pose service must not be left on 8000"
    assert '"--http-port"' in src
