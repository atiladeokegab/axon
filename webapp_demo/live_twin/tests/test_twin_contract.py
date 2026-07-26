import re
from html.parser import HTMLParser
from pathlib import Path


LIVE_TWIN_FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
TWIN_HTML = (LIVE_TWIN_FRONTEND / "twin.html").read_text(encoding="utf-8")


class _ViewportTextParser(HTMLParser):
    """
    Collects text inside the model viewport, tracking whether each run sits
    inside an element that starts out `hidden`. Text that is hidden by default
    is a conditional prompt, not a standing overlay — the two are held to
    different rules below.
    """

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.hidden_depth = 0
        self.always_visible_text: list[str] = []
        self.initially_hidden_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.depth:
            self.depth += 1
            if self.hidden_depth or "hidden" in attrs:
                self.hidden_depth += 1
        elif tag == "section" and attrs.get("id") == "viewport":
            self.depth = 1

    def handle_endtag(self, tag):
        if self.hidden_depth:
            self.hidden_depth -= 1
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth or not data.strip():
            return
        target = self.initially_hidden_text if self.hidden_depth else self.always_visible_text
        target.append(data.strip())


def test_model_viewport_shows_no_text_while_the_arm_is_tracking():
    """
    The viewport is meant to be the model and nothing else during normal use —
    no titles, legends or telemetry readouts competing with the anatomy.
    """
    parser = _ViewportTextParser()
    parser.feed(TWIN_HTML)

    assert parser.always_visible_text == []


def test_viewport_prompt_exists_but_starts_hidden():
    """
    The one exception is the "arm not in frame" prompt: without it a lost arm
    just leaves the last pose frozen on screen, which reads as a crash. It has
    to start hidden so it only ever appears when there is something to say.
    """
    parser = _ViewportTextParser()
    parser.feed(TWIN_HTML)

    assert parser.initially_hidden_text, "expected a hidden prompt inside the viewport"
    assert any("camera" in text.lower() for text in parser.initially_hidden_text)


def test_camera_preview_uses_backend_stream_not_a_second_browser_camera():
    """
    The preview must come from the backend's own capture, so the browser never
    opens a second handle to the physical camera — only one process can hold it.

    Microphone access is a separate matter and is allowed: the conversational
    coach needs it, and it does not contend for the camera. So this checks for
    *video* capture specifically rather than banning getUserMedia outright,
    which is what it used to do.
    """
    assert '<img id="camera-feed"' in TWIN_HTML
    assert "/camera.mjpeg" in TWIN_HTML
    assert "srcObject" not in TWIN_HTML

    for call in re.findall(r"getUserMedia\(\s*\{(.*?)\}\s*\)", TWIN_HTML, re.S):
        assert "video" not in call, f"browser is opening a second camera: getUserMedia({{{call}}})"


def test_live_urls_respect_the_mounted_base_and_keep_overrides():
    assert 'new URL("camera.mjpeg", window.location.href)' in TWIN_HTML
    assert 'new URL(override || "ws", window.location.href)' in TWIN_HTML
    assert 'params.get("camera")' in TWIN_HTML
    assert 'params.get("ws")' in TWIN_HTML
    assert 'replace(/\\/ws\\/?$/, "/camera.mjpeg")' in TWIN_HTML
    assert ":8000/camera.mjpeg" not in TWIN_HTML
    assert "127.0.0.1:8000/ws" not in TWIN_HTML


def test_live_twin_uses_shared_header_and_axon_skin():
    assert 'href="/static/css/global.css"' in TWIN_HTML
    assert '{{ site_header("live-twin") }}' in TWIN_HTML
    assert 'class="session-toolbar"' in TWIN_HTML
    assert 'class="topbar"' not in TWIN_HTML
    assert 'class="wordmark"' not in TWIN_HTML
    assert "--accent:    #87f7c7" in TWIN_HTML
    assert "--r-lg: 0" in TWIN_HTML


def _manifest_ids_by_side() -> dict[str, dict[str, list[str]]]:
    """
    Pull MUSCLE_ASSET_MANIFEST out of twin.html without a JS engine: grab the
    per-side blocks, then the FJ ids listed against each rig target.
    """
    block = TWIN_HTML.split("const MUSCLE_ASSET_MANIFEST = {", 1)[1]
    block = block.split("\n    };", 1)[0]

    sides: dict[str, dict[str, list[str]]] = {}
    current: str | None = None
    for line in block.splitlines():
        side_match = re.match(r"\s{6}(right|left):\s*\{", line)
        if side_match:
            current = side_match.group(1)
            sides[current] = {}
            continue
        target_match = re.match(r"\s{8}(\w+):\s*\[", line)
        if target_match and current:
            sides[current][target_match.group(1)] = []
            _last[0] = target_match.group(1)
        if current and _last[0] and (ids := re.findall(r'"(FJ\w+)"', line)):
            sides[current].setdefault(_last[0], []).extend(ids)
    return sides


_last = [None]


def test_every_manifest_mesh_file_actually_exists():
    """A typo'd id fails silently at runtime — it just leaves capsules up."""
    sides = _manifest_ids_by_side()
    assert sides, "could not parse MUSCLE_ASSET_MANIFEST"

    missing = [
        f"{side}/{target}/{mesh_id}"
        for side, targets in sides.items()
        for target, ids in targets.items()
        for mesh_id in ids
        if not (LIVE_TWIN_FRONTEND / "assets" / "bp3d" / f"{mesh_id}.obj").is_file()
    ]
    assert missing == [], f"manifest references missing mesh files: {missing}"


def test_both_sides_cover_the_same_rig_targets_with_the_same_piece_counts():
    """
    Left and right must stay structurally identical — a side missing a muscle,
    or with a different number of pieces for one, would render an arm that
    silently changes anatomy when the user switches sides.
    """
    sides = _manifest_ids_by_side()

    assert set(sides) == {"right", "left"}
    assert set(sides["right"]) == set(sides["left"])
    for target in sides["right"]:
        assert len(sides["right"][target]) == len(sides["left"][target]), target
    # ...and no id may be shared between sides, which would mean one side is
    # quietly reusing the other's (wrong-handed) geometry.
    assert set(sum(sides["right"].values(), [])).isdisjoint(sum(sides["left"].values(), []))


def test_camera_preview_and_model_are_mirrored_together():
    """
    Mirror mode only works as a pair. When the preview was flipped and the model
    wasn't, raising your right arm sent the preview right and the model left.
    Removing either flip on its own brings that mismatch straight back.
    """
    assert "transform: scaleX(-1);" in TWIN_HTML, "camera preview is no longer mirrored"
    assert "armRoot.scale.x = -1;" in TWIN_HTML, "model is no longer mirrored"


def test_mirroring_stays_out_of_the_landmark_maths():
    """
    The mirror has to remain a presentation transform. Pads are addressed by
    name and the arm side comes from MediaPipe's anatomical landmarks, so a view
    setting must never be able to change which physical pad fires — which it
    could if the flip were folded into the coordinate conversion instead.
    """
    body = TWIN_HTML.split("function shoulderRelative", 1)[1].split("}", 1)[0]

    assert "-(point[0]" not in body, "landmark x is being negated — mirror leaked into the data"
    assert "(point[0] - shoulder[0])" in body


def test_bodyparts3d_attribution_remains_outside_the_model_viewport():
    assert "BodyParts3D © The Database Center for Life Science" in TWIN_HTML
