"""Guarantee the MediaPipe pose model exists before anything tries to open it.

WHY THIS EXISTS. The model is ~5 MB, downloaded rather than committed, and
nothing checked for it. When it was absent the failure surfaced deep inside
MediaPipe's C layer:

    FileNotFoundError: Unable to open file at ...\\models\\pose_landmarker_lite.task

raised from PoseLandmarker.create_from_options, several frames below a
ThreadPoolExecutor, after the camera had already been opened and logged
"camera source '0' opened via MSMF". So the logs said the camera was working
while every request returned 503, which points debugging at the camera and the
webcam permissions rather than at a missing file.

Worse, it fails at the moment a client connects rather than at startup, so the
app looks healthy until someone opens a tab - which in a demo means it looks
healthy until the moment it matters.

This turns that into: fetch it once at startup, or fail immediately with a
message naming the file and the command to get it.
"""

import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)

MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "pose_landmarker_lite.task"
)

# A truncated download is worse than no download: MediaPipe reports it as a
# corrupt-model error rather than an incomplete one. The real file is several
# megabytes, so anything obviously small is treated as junk and re-fetched.
MIN_PLAUSIBLE_BYTES = 1_000_000


def model_is_present(path: Path = MODEL_PATH) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_PLAUSIBLE_BYTES
    except OSError:
        return False


def ensure_pose_model(path: Path = MODEL_PATH, allow_download: bool = True) -> Path:
    """Return the model path, downloading it if needed.

    Set AXON_SKIP_MODEL_DOWNLOAD=1 to forbid the network fetch - useful on a
    machine that is offline, where failing fast with a clear message beats a
    long silent timeout.
    """
    if model_is_present(path):
        return path

    if path.exists():
        # Present but too small: a previous run was interrupted. Remove it, or
        # the next check keeps passing on a corrupt file.
        logger.warning("pose model at %s is truncated (%d bytes) - refetching",
                       path, path.stat().st_size)
        path.unlink(missing_ok=True)

    if not allow_download or os.environ.get("AXON_SKIP_MODEL_DOWNLOAD"):
        raise FileNotFoundError(_missing_message(path))

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("pose model missing - downloading %s -> %s", MODEL_URL, path)
    try:
        # Download to a temporary file in the same directory, then move it into
        # place. An interrupted download must never leave a half-written file
        # at the real path, because the next startup would accept it.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as response, \
                    tmp.open("wb") as out:
                shutil.copyfileobj(response, out)
            if tmp.stat().st_size < MIN_PLAUSIBLE_BYTES:
                raise OSError(
                    "downloaded file is only %d bytes" % tmp.stat().st_size)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FileNotFoundError(_missing_message(path, exc)) from exc

    logger.info("pose model ready: %s (%d bytes)", path, path.stat().st_size)
    return path


def _missing_message(path: Path, exc: Exception | None = None) -> str:
    detail = f"\nThe download failed: {exc}" if exc else ""
    return (
        f"The MediaPipe pose model is missing:\n  {path}\n"
        "Live Twin and Human Control both need it - without it the camera "
        "opens and then every request returns 503.\n"
        "Fetch it with:\n"
        "  uv run python live_twin/scripts/download_pose_model.py"
        f"{detail}"
    )
