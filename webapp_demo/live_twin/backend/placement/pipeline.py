"""
End-to-end pad-placement computation from calibration photos.

Needs 5 stills, all with the camera and arm/body position fixed within each
pair so only the muscle changes between shots:

- relaxed:       side-on, arm bent ~90 deg, sleeves off, muscles relaxed
- bicep_flexed:  same framing as `relaxed`, bicep flexed (e.g. curling in)
- tricep_flexed: same framing as `relaxed`, tricep flexed (e.g. pushing straight)
- front:         front-on, arm slightly raised, shoulder visible
- back:          back-on, arm slightly raised, shoulder visible

Bicep/tricep can't be placed from joint geometry alone — isometric flexion
doesn't move the shoulder/elbow/wrist landmarks, so `relaxed` vs `*_flexed`
tape-dot displacement (markers.py) is what actually locates them. Front/rear
delt use joint-percentage geometry instead, computed separately from the
front and back photos since a single 2D shot can't tell front from back.

Whichever arm side the `relaxed` photo locks onto is reused (not
re-guessed) for the front/back shots, so all 5 photos are guaranteed to
refer to the same physical arm rather than each independently picking
whichever side happens to be more visible in that particular shot.

PlacementError is raised only for malformed input (undecodable image
bytes) — a request-format problem. Detection failures (no arm found, no
flex detected, etc.) never raise; they show up as {"ok": False, ...} in the
per-pad result so a partial/failed calibration is still visible in the
response instead of turning into an all-or-nothing 422.
"""
import cv2
import numpy as np

from live_twin.backend.pose.estimator import detect_pose_in_image
from live_twin.backend.placement.geometry import Point, wrist_pad_point, delt_pad_point
from live_twin.backend.placement import markers
from live_twin.backend.placement import overlay


class PlacementError(ValueError):
    pass


def _decode(image_bytes: bytes, label: str) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise PlacementError(f"could not decode '{label}' image bytes")
    return image


def _as_normalized(point_px: tuple, image_bgr: np.ndarray) -> dict:
    h, w = image_bgr.shape[:2]
    return {"x": float(point_px[0]) / w, "y": float(point_px[1]) / h}


def _point_dict(p: Point) -> dict:
    return {"x": p.x, "y": p.y}


def _ok(point: dict, source_bgr: np.ndarray, label: str, **extra) -> dict:
    annotated = overlay.draw_pad_marker(source_bgr, point, label)
    return {
        "ok": True, "point": point, "detail": None,
        "overlay_b64": overlay.encode_png_b64(annotated),
        **extra,
    }


def _fail(detail: str) -> dict:
    return {"ok": False, "point": None, "detail": detail, "overlay_b64": None}


def compute_placement(relaxed: bytes, bicep_flexed: bytes, tricep_flexed: bytes,
                       front: bytes, back: bytes) -> dict:
    relaxed_bgr = _decode(relaxed, "relaxed")
    bicep_flexed_bgr = _decode(bicep_flexed, "bicep_flexed")
    tricep_flexed_bgr = _decode(tricep_flexed, "tricep_flexed")
    front_bgr = _decode(front, "front")
    back_bgr = _decode(back, "back")

    pads: dict = {}

    relaxed_rgb = cv2.cvtColor(relaxed_bgr, cv2.COLOR_BGR2RGB)
    relaxed_result = detect_pose_in_image(relaxed_rgb)
    locked_side = None

    if relaxed_result is None:
        pads["wrist"] = _fail("no arm confidently detected in the relaxed side-on photo")
    else:
        relaxed_landmarks, locked_side = relaxed_result
        wrist = Point(*relaxed_landmarks["wrist"][:2])
        elbow = Point(*relaxed_landmarks["elbow"][:2])
        pads["wrist"] = _ok(_point_dict(wrist_pad_point(wrist, elbow)), relaxed_bgr, "WRIST")

    for name, flexed_bgr in (("bicep", bicep_flexed_bgr), ("tricep", tricep_flexed_bgr)):
        try:
            point_px, displacement = markers.locate_muscle_flex_point(relaxed_bgr, flexed_bgr)
        except ValueError as exc:
            pads[name] = _fail(str(exc))
            continue
        # displacement was tracked into the flexed frame, so that's the photo the
        # marker actually corresponds to — draw the overlay there, not on relaxed.
        point_norm = _as_normalized(point_px, flexed_bgr)
        entry = _ok(point_norm, flexed_bgr, name.upper(), displacement_px=displacement)
        pads[name] = entry

    if locked_side is None:
        pads["front_delt"] = _fail("no reference arm — relaxed photo detection failed, can't confirm which side to use")
        pads["rear_delt"] = _fail("no reference arm — relaxed photo detection failed, can't confirm which side to use")
    else:
        for name, image_bgr in (("front_delt", front_bgr), ("rear_delt", back_bgr)):
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            result = detect_pose_in_image(image_rgb, side=locked_side)
            if result is None:
                pads[name] = _fail(f"'{locked_side}' arm not confidently detected in this shot")
                continue
            landmarks, _side = result
            shoulder = Point(*landmarks["shoulder"][:2])
            elbow = Point(*landmarks["elbow"][:2])
            pads[name] = _ok(_point_dict(delt_pad_point(shoulder, elbow)), image_bgr, name.upper())

    return {
        "calibration_complete": all(p["ok"] for p in pads.values()),
        "pads": pads,
    }
