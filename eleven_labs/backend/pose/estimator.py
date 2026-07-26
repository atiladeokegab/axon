"""
MediaPipe Pose Landmarker wrapper (Tasks API — the old `mp.solutions.pose`
API is gone as of mediapipe 0.10.35) — extracts the arm landmarks the rest
of the pipeline needs (shoulder, elbow, wrist).

Two entry points:
- ArmPoseEstimator: VIDEO-mode, stateful, for the live webcam stream in main.py.
  Auto mode locks onto one arm side per session (once a side clears the
  visibility threshold, it stays picked unless it drops below threshold)
  instead of re-deciding every frame, which could otherwise flip which arm
  it's tracking mid-session if both are visible. Callers can instead force a
  side via set_side() — a *forced* side never silently falls back to the
  other arm, because a user who asked to train their left arm being quietly
  switched to their right is a safety problem, not a convenience.
- detect_pose_in_image: IMAGE-mode, one-off, for still calibration photos in
  placement/pipeline.py. Accepts an optional `side` to force a specific arm
  (so all 5 calibration photos can be locked to the one the `relaxed` shot
  picked, rather than each photo independently guessing).

Neither returns a low-confidence guess when the arm doesn't clear
MIN_LANDMARK_VISIBILITY. ArmPoseEstimator.read() reports *why* it has no
landmarks (no person in frame vs. person visible but the wanted arm isn't),
so the UI can tell the user what to actually do about it.

Needs a downloaded model file — see scripts/download_pose_model.py.
"""
import pathlib
import time
from dataclasses import dataclass

import mediapipe as mp
from mediapipe.tasks.python import vision

from backend import config

SIDE_LEFT = "left"
SIDE_RIGHT = "right"

LEFT_INDICES = {
    "shoulder": vision.PoseLandmark.LEFT_SHOULDER,
    "elbow": vision.PoseLandmark.LEFT_ELBOW,
    "wrist": vision.PoseLandmark.LEFT_WRIST,
}
RIGHT_INDICES = {
    "shoulder": vision.PoseLandmark.RIGHT_SHOULDER,
    "elbow": vision.PoseLandmark.RIGHT_ELBOW,
    "wrist": vision.PoseLandmark.RIGHT_WRIST,
}
_SIDE_INDICES = {SIDE_LEFT: LEFT_INDICES, SIDE_RIGHT: RIGHT_INDICES}
SIDES = (SIDE_LEFT, SIDE_RIGHT)

# Why a frame produced no landmarks. The distinction matters to the user:
# "no person" means step into frame, "arm not visible" means the person is
# there but the arm being tracked isn't raised/uncovered enough.
STATUS_TRACKING = "tracking"
STATUS_NO_PERSON = "no_person"
STATUS_ARM_NOT_VISIBLE = "arm_not_visible"

# Anchored to the repo rather than the cwd: MediaPipe resolves a relative path
# against wherever the process was launched, so a cwd-relative default fails
# with a bare FileNotFoundError the moment the server is started from anywhere
# other than eleven_labs/. Callers can still pass an explicit path.
DEFAULT_MODEL_PATH = str(
    pathlib.Path(__file__).resolve().parents[2] / "models" / "pose_landmarker_lite.task"
)


@dataclass(frozen=True)
class PoseReading:
    """
    One frame's outcome. Both landmark sets are None unless status is tracking.

    `landmarks` are MediaPipe's normalized image coordinates — what the twin
    renders, since it only needs relative shape. `world_landmarks` are the
    metric ones (metres, origin at the hip midpoint), which is what the control
    loop's contract requires; normalized coords would silently be wrong there
    because they carry no scale. See POSE_API.md.
    """
    landmarks: dict | None
    side: str | None
    status: str
    world_landmarks: dict | None = None


def validate_side(side: str | None) -> None:
    """
    Raises ValueError unless `side` is 'left', 'right', or None (auto).
    Shared so callers can reject a bad side at the edge — before it gets
    stored and silently re-applied later — rather than only at use time.
    """
    if side is not None and side not in _SIDE_INDICES:
        raise ValueError(f"side must be one of {SIDES} or None, got {side!r}")


def _visibility(landmarks, indices: dict) -> float:
    return sum(landmarks[idx].visibility for idx in indices.values()) / len(indices)


def _extract(landmarks, indices: dict) -> dict:
    return {
        name: (landmarks[idx].x, landmarks[idx].y, landmarks[idx].z)
        for name, idx in indices.items()
    }


def _pick_side(landmarks, preferred: str | None = None) -> tuple[str, float] | None:
    """
    Returns (side, visibility) for whichever side is confidently visible,
    preferring `preferred` if it still clears the threshold (so a locked
    side doesn't flip just because the other side is marginally better this
    frame). Returns None if neither side clears MIN_LANDMARK_VISIBILITY —
    treat that as tracking lost, not a cue to guess.
    """
    left_v = _visibility(landmarks, LEFT_INDICES)
    right_v = _visibility(landmarks, RIGHT_INDICES)

    if preferred is not None:
        preferred_v = left_v if preferred == SIDE_LEFT else right_v
        if preferred_v >= config.MIN_LANDMARK_VISIBILITY:
            return preferred, preferred_v

    if left_v < config.MIN_LANDMARK_VISIBILITY and right_v < config.MIN_LANDMARK_VISIBILITY:
        return None

    return (SIDE_LEFT, left_v) if left_v >= right_v else (SIDE_RIGHT, right_v)


class ArmPoseEstimator:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH,
                 min_detection_confidence: float = 0.5,
                 min_tracking_confidence: float = 0.5,
                 side: str | None = None):
        base_options = mp.tasks.BaseOptions(model_asset_path=model_path)
        options = vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            min_pose_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._start_time = time.monotonic()
        self._locked_side: str | None = None
        self._forced_side: str | None = None
        self.set_side(side)

    def set_side(self, side: str | None) -> None:
        """
        Force tracking to one arm, or pass None to go back to auto-picking.
        Raises ValueError on an unknown side rather than silently tracking
        the wrong arm.
        """
        validate_side(side)
        self._forced_side = side
        # Drop any auto-lock so a switch takes effect on the very next frame
        # instead of the previous lock outvoting the user's choice.
        self._locked_side = None

    @property
    def side(self) -> str | None:
        """The side actually being tracked, forced or auto-locked."""
        return self._forced_side or self._locked_side

    def read(self, frame_rgb) -> PoseReading:
        """
        One frame. On success, landmarks are
        {"shoulder": (x, y, z), "elbow": (x, y, z), "wrist": (x, y, z)} in
        normalized MediaPipe coordinates; otherwise status says why not.
        """
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        timestamp_ms = int((time.monotonic() - self._start_time) * 1000)
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.pose_landmarks:
            return PoseReading(None, self._forced_side, STATUS_NO_PERSON)

        landmarks = result.pose_landmarks[0]
        # Same detection, second representation. Absent on some builds//frames,
        # so treated as optional: the twin only needs the normalized set, and the
        # UDP feed simply doesn't send a frame it has no metric data for.
        world = result.pose_world_landmarks[0] if result.pose_world_landmarks else None

        def tracking(indices, side):
            return PoseReading(
                _extract(landmarks, indices),
                side,
                STATUS_TRACKING,
                _extract(world, indices) if world is not None else None,
            )

        if self._forced_side is not None:
            # Deliberately not _pick_side(): that falls back to the other arm
            # once the preferred one dims, which is right for an auto-lock but
            # wrong for an explicit user choice.
            indices = _SIDE_INDICES[self._forced_side]
            if _visibility(landmarks, indices) < config.MIN_LANDMARK_VISIBILITY:
                return PoseReading(None, self._forced_side, STATUS_ARM_NOT_VISIBLE)
            return tracking(indices, self._forced_side)

        picked = _pick_side(landmarks, preferred=self._locked_side)
        if picked is None:
            return PoseReading(None, None, STATUS_ARM_NOT_VISIBLE)

        side, _visibility_score = picked
        self._locked_side = side
        return tracking(_SIDE_INDICES[side], side)

    def process(self, frame_rgb) -> dict | None:
        """Back-compat shim: landmarks only, None when tracking is lost."""
        return self.read(frame_rgb).landmarks

    def close(self):
        self._landmarker.close()


def detect_pose_in_image(frame_rgb, model_path: str = DEFAULT_MODEL_PATH,
                          side: str | None = None) -> tuple[dict, str] | None:
    """
    One-off pose detection on a single still image. Returns
    (landmarks_dict, side_used) or None if tracking is lost.

    Pass `side` (SIDE_LEFT/SIDE_RIGHT) to force that specific arm rather than
    auto-picking — used by the placement pipeline to keep all 5 calibration
    photos locked to the same physical arm.
    """
    base_options = mp.tasks.BaseOptions(model_asset_path=model_path)
    options = vision.PoseLandmarkerOptions(base_options=base_options, running_mode=vision.RunningMode.IMAGE)
    landmarker = vision.PoseLandmarker.create_from_options(options)
    try:
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = landmarker.detect(mp_image)
        if not result.pose_landmarks:
            return None
        landmarks = result.pose_landmarks[0]

        if side is not None:
            if _visibility(landmarks, _SIDE_INDICES[side]) < config.MIN_LANDMARK_VISIBILITY:
                return None
            return _extract(landmarks, _SIDE_INDICES[side]), side

        picked = _pick_side(landmarks)
        if picked is None:
            return None
        chosen_side, _ = picked
        return _extract(landmarks, _SIDE_INDICES[chosen_side]), chosen_side
    finally:
        landmarker.close()
