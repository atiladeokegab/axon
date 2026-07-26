import cv2
import numpy as np
from ems_muscle_mapper.schemas import MuscleAnalysisResult

# 3c. OpenCV Render Engine

def _hex_to_bgr(hex_color: str):
    """Converts standard HEX code to OpenCV's BGR color space."""
    hex_color = hex_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return (rgb[2], rgb[1], rgb[0])


def _boxes_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def _choose_label_position(
    text: str,
    pointer: tuple[int, int],
    font_scale: float,
    image_width: int,
    image_height: int,
    occupied: list[tuple[int, int, int, int]],
) -> tuple[tuple[int, int], tuple[int, int, int, int]]:
    """
    Find a non-overlapping label position.

    Pointer-right is preferred, pointer-left is tried second, then the same two
    sides are searched at increasing vertical offsets.
    """
    (text_width, text_height), baseline = cv2.getTextSize(
        text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2
    )
    pointer_x, pointer_y = pointer
    horizontal_gap = 18
    padding = 6
    vertical_step = text_height + baseline + padding * 2
    vertical_offsets = [0]
    for multiplier in range(1, max(2, image_height // max(1, vertical_step)) + 1):
        vertical_offsets.extend(
            [-multiplier * vertical_step, multiplier * vertical_step]
        )

    fallback = None
    fallback_overlap = None
    for vertical_offset in vertical_offsets:
        baseline_y = pointer_y + text_height // 2 + vertical_offset
        origins = [
            (pointer_x + horizontal_gap, baseline_y),
            (pointer_x - horizontal_gap - text_width, baseline_y),
        ]
        for origin in origins:
            x, y = origin
            box = (
                x - padding,
                y - text_height - padding,
                x + text_width + padding,
                y + baseline + padding,
            )
            inside_image = (
                box[0] >= 0
                and box[1] >= 0
                and box[2] <= image_width
                and box[3] <= image_height
            )
            overlap_count = sum(
                _boxes_overlap(box, previous) for previous in occupied
            )
            if inside_image and overlap_count == 0:
                return origin, box
            if inside_image and (
                fallback_overlap is None or overlap_count < fallback_overlap
            ):
                fallback = (origin, box)
                fallback_overlap = overlap_count

    if fallback is not None:
        return fallback

    # Extremely small images may not fit the text at full size. Keep the
    # baseline visible; callers still receive deterministic bounds.
    x = min(max(padding, pointer_x + horizontal_gap), max(padding, image_width - text_width - padding))
    y = min(max(text_height + padding, pointer_y), max(text_height + padding, image_height - baseline - padding))
    box = (
        x - padding,
        y - text_height - padding,
        x + text_width + padding,
        y + baseline + padding,
    )
    return (x, y), box


def draw_ems_ui(image_bytes: bytes, analysis: MuscleAnalysisResult) -> bytes:
    """Overlays polygons, dots, and labels over the flexed image."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("The normalized flexed image could not be decoded.")
    overlay = img.copy()
    
    h, w = img.shape[:2]
    label_scale = max(0.8, min(1.5, w / 1000.0))
    
    # 1. Draw Transparent Polygons first
    for muscle in analysis.muscles:
        color_bgr = _hex_to_bgr(muscle.color_hex)
        pts = np.array(
            [
                [
                    min(w - 1, max(0, int(pt.x * w))),
                    min(h - 1, max(0, int(pt.y * h))),
                ]
                for pt in muscle.polygon_vertices_normalized
            ],
            np.int32,
        )
        pts = pts.reshape((-1, 1, 2))
        cv2.fillPoly(overlay, [pts], color_bgr)
        
    # Blend overlay with original image
    alpha = 0.4
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)
    
    # 2. Draw Solid UI Elements (Muscle and EMS pad labels only).
    occupied_labels: list[tuple[int, int, int, int]] = []
    for muscle in analysis.muscles:
        color_bgr = _hex_to_bgr(muscle.color_hex)
        
        # Muscle Labels
        if len(muscle.polygon_vertices_normalized) > 0:
            lbl_x = int(
                np.mean(
                    [point.x for point in muscle.polygon_vertices_normalized]
                )
                * w
            )
            lbl_y = int(
                np.mean(
                    [point.y for point in muscle.polygon_vertices_normalized]
                )
                * h
            )
            label_origin, label_bounds = _choose_label_position(
                muscle.name,
                (lbl_x, lbl_y),
                label_scale,
                w,
                h,
                occupied_labels,
            )
            occupied_labels.append(label_bounds)
            cv2.line(img, (lbl_x, lbl_y), label_origin, color_bgr, 2)
            cv2.putText(img, muscle.name, label_origin, cv2.FONT_HERSHEY_SIMPLEX, label_scale, (255, 255, 255), 4)
            cv2.putText(img, muscle.name, label_origin, cv2.FONT_HERSHEY_SIMPLEX, label_scale, color_bgr, 2)
        
        # EMS Pad Markers
        for pad in muscle.ems_pads_normalized:
            pad_x = min(w - 1, max(0, int(pad.x * w)))
            pad_y = min(h - 1, max(0, int(pad.y * h)))
            # White inner dot, distinct colored outer ring
            cv2.circle(img, (pad_x, pad_y), 8, (255, 255, 255), -1) 
            cv2.circle(img, (pad_x, pad_y), 12, (0, 150, 255), 3)     
            
            # Pad Labels (e.g., "Proximal")
            label_origin, label_bounds = _choose_label_position(
                pad.label,
                (pad_x, pad_y),
                label_scale,
                w,
                h,
                occupied_labels,
            )
            occupied_labels.append(label_bounds)
            cv2.line(img, (pad_x, pad_y), label_origin, (0, 150, 255), 2)
            cv2.putText(img, pad.label, label_origin, cv2.FONT_HERSHEY_SIMPLEX, label_scale, (0, 0, 0), 4)
            cv2.putText(img, pad.label, label_origin, cv2.FONT_HERSHEY_SIMPLEX, label_scale, (255, 255, 255), 2)
            
    _, encoded_img = cv2.imencode('.jpg', img)
    return encoded_img.tobytes()


def build_alt_text(analysis: MuscleAnalysisResult) -> str:
    """Build an accessible text description for the annotated image."""
    if not analysis.muscles:
        return (
            f"Movement detected: {analysis.movement_detected}.\n"
            "Highlighted muscles: None."
        )

    muscle_descriptions = []
    for muscle in analysis.muscles:
        pad_labels = ", ".join(pad.label for pad in muscle.ems_pads_normalized)
        if pad_labels:
            muscle_descriptions.append(
                f"{muscle.name}, with EMS pads marked at {pad_labels}"
            )
        else:
            muscle_descriptions.append(muscle.name)

    return (
        f"Movement detected: {analysis.movement_detected}.\n"
        f"Highlighted muscles: {'; '.join(muscle_descriptions)}."
    )
