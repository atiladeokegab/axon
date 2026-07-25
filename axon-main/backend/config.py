"""
Shared config / assumptions for the motor-recovery backend.

Everything in ASSUMED_* below is a placeholder pending team confirmation.
See README.md "Open items" for what's confirmed vs guessed.
"""
import os

# --- Board connection (ASSUMED — need real IP/port from Jack/Faisal) ---
_PLACEHOLDER_BOARD_HOST = "192.168.1.100"
BOARD_HOST = os.environ.get("BOARD_HOST", _PLACEHOLDER_BOARD_HOST)
BOARD_PORT = int(os.environ.get("BOARD_PORT", "5005"))

# Mock mode: when True, the driver never opens a real socket — it just logs
# what it would have sent. Default ON. Only flip to False once the board's
# JSON contract and the 4th relay pairing are both confirmed for real.
DRIVER_MOCK_MODE = os.environ.get("DRIVER_MOCK_MODE", "true").lower() != "false"

# These two must be explicitly flipped to "true" (separately from turning off
# mock mode) before any real command reaches the board — see
# assert_real_mode_allowed() below. Two independent switches on purpose: one
# accidental env change shouldn't be enough to arm real hardware.
BOARD_CONTRACT_CONFIRMED = os.environ.get("BOARD_CONTRACT_CONFIRMED", "false").lower() == "true"
RELAY_PAIRING_CONFIRMED = os.environ.get("RELAY_PAIRING_CONFIRMED", "false").lower() == "true"

# --- Pose output to the teammates' control loop (see POSE_API.md) ---
# A separate UDP feed, deliberately not the WebSocket the UI uses: that one
# publishes every frame including "arm not visible" so the UI can prompt the
# user, whereas this contract requires SILENCE when tracking is lost, because
# silence is what safely stops their stimulation. Never merge the two paths.
#
# Off by default: enabling it starts sending pose to whatever is on the other
# end, which on their rig drives a limb.
POSE_UDP_ENABLED = os.environ.get("POSE_UDP_ENABLED", "false").lower() == "true"
POSE_UDP_HOST = os.environ.get("POSE_UDP_HOST", "127.0.0.1")
POSE_UDP_PORT = int(os.environ.get("POSE_UDP_PORT", "9090"))
# Their receiver treats poses older than 300ms as stale; keep well inside that.
POSE_UDP_MAX_DATAGRAM_BYTES = 2048

# --- Pad names ---
# Wrist pair is ASSUMED (flexor/extensor antagonist pattern, matching the
# other two pairs) — NOT confirmed with Jack. Do not fire WRIST_EXTEND for
# real until he confirms what's actually wired to that relay output.
PAD_WRIST_FLEX = "WRIST_FLEX"
PAD_WRIST_EXTEND = "WRIST_EXTEND"          # ASSUMED pairing, unconfirmed
PAD_BICEP = "BICEP"
PAD_TRICEP = "TRICEP"
PAD_FRONT_DELT = "FRONT_DELT"
PAD_REAR_DELT = "REAR_DELT"

# Relay output pairing (2 pads share one relay output, antagonist muscles).
# Confirmed: BICEP/TRICEP, FRONT_DELT/REAR_DELT.
# Assumed: WRIST_FLEX/WRIST_EXTEND — confirm with Jack before real firing.
RELAY_PAIRS = {
    "output_1": (PAD_WRIST_FLEX, PAD_WRIST_EXTEND),   # ASSUMED
    "output_2": (PAD_BICEP, PAD_TRICEP),
    "output_3": (PAD_FRONT_DELT, PAD_REAR_DELT),
}

# The pad-name allowlist: this is the single source of truth for "a pad name
# the driver is allowed to command." Nothing should reach the board (real or
# mock) with a pad name that isn't wired to a known relay output.
ALL_PAD_NAMES = frozenset(pad for pair in RELAY_PAIRS.values() for pad in pair)

# --- Motion -> pad mapping ---
MOTION_GRIP = "grip"
MOTION_RAISE = "raise"
MOTION_LOWER = "lower"
MOTION_PUSH_FORWARD = "push_forward"
MOTION_PULL_BACK = "pull_back"

MOTION_TO_PAD = {
    MOTION_GRIP: PAD_WRIST_FLEX,
    MOTION_RAISE: PAD_BICEP,
    MOTION_LOWER: PAD_TRICEP,
    MOTION_PUSH_FORWARD: PAD_FRONT_DELT,
    MOTION_PULL_BACK: PAD_REAR_DELT,
}

# --- Actuation defaults + safety bounds ---
DEFAULT_INTENSITY = 60          # 0-100, ASSUMED scale
DEFAULT_DURATION_MS = 800       # ASSUMED

# Hard bounds enforced by both TensClient (defense in depth) and
# ActuationController (primary check) — reject anything outside these rather
# than trusting every caller to pass sane values.
MIN_INTENSITY = 0
MAX_INTENSITY = 100
MIN_DURATION_MS = 100
MAX_DURATION_MS = 2000

# Minimum time between one fire command completing and the next being
# accepted — a basic rate limit so a buggy or malicious client can't spam
# the board with back-to-back stimulation commands.
ACTUATION_COOLDOWN_S = 1.0

# --- Placement geometry offsets (ASSUMED, unconfirmed with Siona/Amara) ---
# Env-overridable so a real number from Siona/Amara can be dropped in without
# a code change/redeploy.
# Fraction of forearm length (wrist->elbow), measured from the wrist landmark
# toward the elbow landmark.
WRIST_PAD_OFFSET_PCT = float(os.environ.get("WRIST_PAD_OFFSET_PCT", "0.08"))

# Fraction of upper-arm length (shoulder->elbow), measured from the shoulder
# landmark, used to place front/rear delt pads (front-on photo only).
DELT_PAD_OFFSET_PCT = float(os.environ.get("DELT_PAD_OFFSET_PCT", "0.10"))

# --- Camera / capture ---
# "webcam" for a local OpenCV device index, or an http(s) URL for a phone
# streaming app (e.g. IP Webcam on the Pixel 7: "http://<phone-ip>:8080/video")
VIDEO_SOURCE = os.environ.get("VIDEO_SOURCE", "0")

# --- Tape-dot marker detection (bicep/tricep flex localization) ---
# HSV range for the marker tape colour — tune once you know the actual tape
# (see scripts/calibrate_tape_color.py). Env-overridable as "H,S,V" so this
# can be tuned on-site without a code change/redeploy.
def _parse_hsv_env(name: str, default: tuple) -> tuple:
    raw = os.environ.get(name)
    if raw is None:
        return default
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3:
        raise ValueError(f"{name} must be 3 comma-separated H,S,V ints, got {raw!r}")
    try:
        return tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"{name} must be 3 comma-separated H,S,V ints, got {raw!r}") from exc


MARKER_HSV_LOWER = _parse_hsv_env("MARKER_HSV_LOWER", (140, 80, 80))   # placeholder: pink/magenta tape
MARKER_HSV_UPPER = _parse_hsv_env("MARKER_HSV_UPPER", (170, 255, 255))

# Contour area bounds (pixels) for something to count as a tape dot rather
# than noise or a stray patch of similarly-coloured skin/background.
MARKER_MIN_DOT_AREA_PX = 8
MARKER_MAX_DOT_AREA_PX = 5000

# A tracked dot must move at least this many pixels between relaxed and
# flexed shots to count as a real flex signal, not camera noise/jitter.
MARKER_MIN_FLEX_DISPLACEMENT_PX = 3.0

# --- Pose landmark confidence ---
# Average per-landmark visibility required to trust a detected arm side.
# Below this, treat it as "tracking lost" and return None rather than a
# low-confidence guess at coordinates.
MIN_LANDMARK_VISIBILITY = 0.5


def assert_real_mode_allowed(host: str | None = None) -> None:
    """
    Raises RuntimeError if the driver is about to run in real (non-mock)
    mode without every precondition explicitly confirmed. Called by
    TensClient whenever it's constructed with mock=False — real hardware
    fires a TENS pad against a person, so this must fail loudly rather than
    silently sending unverified commands.

    Checks the actual host that will be used (`host`, defaulting to the
    module-level BOARD_HOST) rather than only the global — TensClient
    accepts a host override, and the placeholder check must follow whatever
    host is actually about to be dialed, not just the default.
    """
    checked_host = BOARD_HOST if host is None else host

    problems = []
    if checked_host == _PLACEHOLDER_BOARD_HOST:
        problems.append("BOARD_HOST is still the placeholder default — set the real board IP")
    if not BOARD_CONTRACT_CONFIRMED:
        problems.append(
            "BOARD_CONTRACT_CONFIRMED is not set — the board's JSON field names/ack "
            "format haven't been confirmed with Jack/Faisal"
        )
    if not RELAY_PAIRING_CONFIRMED:
        problems.append(
            "RELAY_PAIRING_CONFIRMED is not set — the wrist flexor/extensor relay "
            "pairing is still an assumption, not confirmed on real hardware"
        )
    if problems:
        raise RuntimeError(
            "Refusing to run the TENS driver in real mode:\n- " + "\n- ".join(problems) +
            "\nSet DRIVER_MOCK_MODE=true to keep testing safely, or resolve the above "
            "and set BOARD_HOST/BOARD_CONTRACT_CONFIRMED/RELAY_PAIRING_CONFIRMED."
        )
