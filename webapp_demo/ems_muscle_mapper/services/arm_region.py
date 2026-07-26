from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from ems_muscle_mapper.schemas import MuscleAnalysisResult
from ems_muscle_mapper.services.arm_validator import pose_model


ArmSide = Literal["left", "right"]
ImageSide = Literal["left", "right", "uncertain"]


class ArmNotFoundError(ValueError):
    """Raised when shoulder, elbow, and wrist cannot define a reliable arm crop."""


@dataclass(frozen=True)
class ArmRegion:
    image_bytes: bytes
    side: ArmSide
    left: int
    top: int
    right: int
    bottom: int
    source_width: int
    source_height: int
    shoulder_crop: tuple[float, float] | None = None
    elbow_crop: tuple[float, float] | None = None
    wrist_crop: tuple[float, float] | None = None
    flexion_side: ImageSide = "uncertain"

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def map_analysis_to_source(
        self, analysis: MuscleAnalysisResult
    ) -> MuscleAnalysisResult:
        """Map crop-normalized coordinates back to normalized source coordinates."""
        mapped = analysis.model_copy(deep=True)

        def map_x(value: float) -> float:
            crop_x = min(1.0, max(0.0, value))
            return min(
                1.0,
                max(0.0, (self.left + crop_x * self.width) / self.source_width),
            )

        def map_y(value: float) -> float:
            crop_y = min(1.0, max(0.0, value))
            return min(
                1.0,
                max(0.0, (self.top + crop_y * self.height) / self.source_height),
            )

        for muscle in mapped.muscles:
            for point in muscle.polygon_vertices_normalized:
                point.x = map_x(point.x)
                point.y = map_y(point.y)
            for pad in muscle.ems_pads_normalized:
                pad.x = map_x(pad.x)
                pad.y = map_y(pad.y)

        return mapped

    def map_analysis_to_crop(
        self, analysis: MuscleAnalysisResult
    ) -> MuscleAnalysisResult:
        """Map source-normalized coordinates into this arm crop."""
        mapped = analysis.model_copy(deep=True)

        def map_x(value: float) -> float:
            source_x = min(1.0, max(0.0, value)) * self.source_width
            return min(1.0, max(0.0, (source_x - self.left) / self.width))

        def map_y(value: float) -> float:
            source_y = min(1.0, max(0.0, value)) * self.source_height
            return min(1.0, max(0.0, (source_y - self.top) / self.height))

        for muscle in mapped.muscles:
            for point in muscle.polygon_vertices_normalized:
                point.x = map_x(point.x)
                point.y = map_y(point.y)
            for pad in muscle.ems_pads_normalized:
                pad.x = map_x(pad.x)
                pad.y = map_y(pad.y)

        return mapped

    def pose_prompt_context(self) -> str:
        """Describe the detected anatomy in crop-normalized coordinates."""
        if not self.shoulder_crop or not self.elbow_crop or not self.wrist_crop:
            return (
                f"YOLO identifies this as the subject's {self.side} arm. "
                "Detailed joint geometry is unavailable."
            )

        context = (
            f"YOLO identifies this as the subject's {self.side} arm. "
            f"In second-crop coordinates, shoulder={_format_point(self.shoulder_crop)}, "
            f"elbow={_format_point(self.elbow_crop)}, and "
            f"wrist={_format_point(self.wrist_crop)}. "
            "The wrist may lie outside the upper-arm crop."
        )
        if self.flexion_side != "uncertain":
            context += (
                f" The forearm bends toward the {self.flexion_side} side of the "
                "crop. For elbow flexion, biceps belongs on that flexion/anterior "
                "side and triceps on the opposite side. Do not reverse them."
            )
        return context

    def refine_crop_analysis(
        self, analysis: MuscleAnalysisResult
    ) -> MuscleAnalysisResult:
        """Preserve the model's natural contour while correcting muscle labels."""
        refined = analysis.model_copy(deep=True)
        self._correct_biceps_triceps_labels(refined)
        self._snap_named_muscles_to_arm_edge(refined)
        return refined

    def _snap_named_muscles_to_arm_edge(
        self, analysis: MuscleAnalysisResult
    ) -> None:
        """
        Move only each muscle's outer polygon chain onto the visible arm edge.

        This retains the model's irregular vertex order and inner contour while
        avoiding regular, center-biased polygons that stop short of the skin
        silhouette.
        """
        if (
            not self.shoulder_crop
            or not self.elbow_crop
            or self.flexion_side == "uncertain"
        ):
            return

        image_array = np.frombuffer(self.image_bytes, np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if image is None:
            return

        height, width = image.shape[:2]
        shoulder = np.array(
            [self.shoulder_crop[0] * width, self.shoulder_crop[1] * height],
            dtype=float,
        )
        elbow = np.array(
            [self.elbow_crop[0] * width, self.elbow_crop[1] * height],
            dtype=float,
        )
        axis = elbow - shoulder
        axis_length = float(np.linalg.norm(axis))
        if axis_length < 2.0:
            return

        crop_right_normal = np.array([axis[1], -axis[0]]) / axis_length
        lab_image = cv2.GaussianBlur(
            cv2.cvtColor(image, cv2.COLOR_BGR2LAB), (0, 0), 2
        )

        for muscle in analysis.muscles:
            name = muscle.name.lower()
            if "bicep" in name:
                edge_side = self.flexion_side
            elif "tricep" in name:
                edge_side = "left" if self.flexion_side == "right" else "right"
            else:
                continue

            _snap_outer_polygon_chain(
                muscle.polygon_vertices_normalized,
                lab_image,
                shoulder,
                axis,
                crop_right_normal,
                edge_side,
            )
            _align_pads_to_polygon_centerline(
                muscle.polygon_vertices_normalized,
                muscle.ems_pads_normalized,
                shoulder,
                axis,
                crop_right_normal,
                width,
                height,
            )

    def _correct_biceps_triceps_labels(
        self, analysis: MuscleAnalysisResult
    ) -> None:
        if (
            self.flexion_side == "uncertain"
            or not self.shoulder_crop
            or not self.elbow_crop
        ):
            return

        biceps = next(
            (muscle for muscle in analysis.muscles if "bicep" in muscle.name.lower()),
            None,
        )
        triceps = next(
            (muscle for muscle in analysis.muscles if "tricep" in muscle.name.lower()),
            None,
        )
        if not biceps or not triceps:
            return

        biceps_score = self._lateral_score(biceps.polygon_vertices_normalized)
        triceps_score = self._lateral_score(triceps.polygon_vertices_normalized)
        if biceps_score is None or triceps_score is None:
            return

        # Positive score means crop-right. Biceps must be on the flexion side.
        desired_sign = 1.0 if self.flexion_side == "right" else -1.0
        if biceps_score * desired_sign < triceps_score * desired_sign:
            biceps.name, triceps.name = triceps.name, biceps.name

    def _lateral_score(self, points) -> float | None:
        if not points or not self.shoulder_crop or not self.elbow_crop:
            return None

        shoulder = np.array(self.shoulder_crop, dtype=float)
        elbow = np.array(self.elbow_crop, dtype=float)
        axis = elbow - shoulder
        length = float(np.linalg.norm(axis))
        if length < 1e-6:
            return None

        centroid = np.mean([[point.x, point.y] for point in points], axis=0)
        midpoint = (shoulder + elbow) / 2.0
        crop_right_normal = np.array([axis[1], -axis[0]]) / length
        return float(np.dot(centroid - midpoint, crop_right_normal))


def extract_arm_region(
    image_bytes: bytes,
    preferred_side: ArmSide | None = None,
    confidence_threshold: float = 0.5,
) -> ArmRegion:
    """
    Crop the upper-arm region from the best shoulder-elbow-wrist detection.

    Wrist confidence is still required to establish a complete, unambiguous arm,
    but the VLM crop is constrained to shoulder-to-elbow where the primary
    elbow-flexion muscles and EMS pads are located.
    """
    image_array = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ArmNotFoundError("The normalized image could not be decoded.")

    results = pose_model(image, verbose=False)
    if not results or results[0].keypoints is None:
        raise ArmNotFoundError(_arm_error_message(preferred_side))

    keypoints_data = results[0].keypoints.data.cpu().numpy()
    if len(keypoints_data) == 0:
        raise ArmNotFoundError(_arm_error_message(preferred_side))

    # COCO indices: left shoulder/elbow/wrist = 5/7/9,
    # right shoulder/elbow/wrist = 6/8/10.
    definitions: dict[ArmSide, tuple[int, int, int]] = {
        "left": (5, 7, 9),
        "right": (6, 8, 10),
    }
    sides = [preferred_side] if preferred_side else ["left", "right"]
    candidates = []

    for person_keypoints in keypoints_data:
        for side in sides:
            indices = definitions[side]
            points = person_keypoints[list(indices)]
            confidences = points[:, 2]
            if np.all(confidences >= confidence_threshold):
                candidates.append((float(np.mean(confidences)), side, points[:, :2]))

    if not candidates:
        raise ArmNotFoundError(_arm_error_message(preferred_side))

    _, side, points = max(candidates, key=lambda candidate: candidate[0])
    source_height, source_width = image.shape[:2]

    shoulder, elbow, wrist = points
    crop_points = np.array([shoulder, elbow])
    upper_arm_length = float(np.linalg.norm(shoulder - elbow))
    padding = max(24, int(round(upper_arm_length * 0.22)))

    left = max(0, int(np.floor(np.min(crop_points[:, 0]))) - padding)
    top = max(0, int(np.floor(np.min(crop_points[:, 1]))) - padding)
    right = min(
        source_width, int(np.ceil(np.max(crop_points[:, 0]))) + padding + 1
    )
    bottom = min(
        source_height, int(np.ceil(np.max(crop_points[:, 1]))) + padding + 1
    )

    if right - left < 2 or bottom - top < 2:
        raise ArmNotFoundError(_arm_error_message(preferred_side))

    crop = image[top:bottom, left:right]
    encoded, buffer = cv2.imencode(
        ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    if not encoded:
        raise ArmNotFoundError("The detected arm region could not be encoded.")

    def to_crop_coordinates(point: np.ndarray) -> tuple[float, float]:
        return (
            float((point[0] - left) / (right - left)),
            float((point[1] - top) / (bottom - top)),
        )

    return ArmRegion(
        image_bytes=buffer.tobytes(),
        side=side,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        source_width=source_width,
        source_height=source_height,
        shoulder_crop=to_crop_coordinates(shoulder),
        elbow_crop=to_crop_coordinates(elbow),
        wrist_crop=to_crop_coordinates(wrist),
        flexion_side=_infer_flexion_side(shoulder, elbow, wrist),
    )


def _infer_flexion_side(
    shoulder: np.ndarray, elbow: np.ndarray, wrist: np.ndarray
) -> ImageSide:
    upper_arm = elbow - shoulder
    length = float(np.linalg.norm(upper_arm))
    if length < 1.0:
        return "uncertain"

    midpoint = (shoulder + elbow) / 2.0
    image_right_normal = np.array([upper_arm[1], -upper_arm[0]])
    signed_distance = float(np.dot(wrist - midpoint, image_right_normal) / length)
    if abs(signed_distance) < max(5.0, length * 0.03):
        return "uncertain"
    return "right" if signed_distance > 0 else "left"


def _snap_outer_polygon_chain(
    points,
    lab_image: np.ndarray,
    shoulder: np.ndarray,
    axis: np.ndarray,
    crop_right_normal: np.ndarray,
    edge_side: Literal["left", "right"],
) -> None:
    """Snap the outward-facing polygon chain to real image-edge gradients."""
    if len(points) < 4:
        return

    height, width = lab_image.shape[:2]
    coordinates = np.array(
        [[point.x * width, point.y * height] for point in points], dtype=float
    )
    axis_squared = float(np.dot(axis, axis))
    axis_length = float(np.sqrt(axis_squared))
    axial_positions = ((coordinates - shoulder) @ axis) / axis_squared
    lateral_positions = (coordinates - shoulder) @ crop_right_normal
    side_sign = 1.0 if edge_side == "right" else -1.0

    proximal_index = int(np.argmin(axial_positions))
    distal_index = int(np.argmax(axial_positions))
    if proximal_index == distal_index:
        return

    forward_chain = _cyclic_chain_indices(
        proximal_index, distal_index, len(points), step=1
    )
    backward_chain = _cyclic_chain_indices(
        proximal_index, distal_index, len(points), step=-1
    )

    def chain_score(indices: list[int]) -> float:
        interior = indices[1:-1] or indices
        return float(
            np.mean([lateral_positions[index] * side_sign for index in interior])
        )

    outer_chain = max((forward_chain, backward_chain), key=chain_score)
    direction = crop_right_normal * side_sign

    for index in outer_chain:
        axial_position = float(axial_positions[index])
        if not 0.05 <= axial_position <= 0.9:
            continue

        axis_point = shoulder + axis * axial_position
        edge_point = _find_arm_edge_along_normal(
            lab_image, axis_point, direction, axis_length
        )
        if edge_point is None:
            continue

        current_outward_distance = float(
            np.dot(coordinates[index] - axis_point, direction)
        )
        edge_outward_distance = float(np.dot(edge_point - axis_point, direction))
        # Never pull a model vertex inward; the detected edge is used only to
        # extend a center-biased outline toward the visible silhouette.
        if edge_outward_distance <= current_outward_distance + axis_length * 0.015:
            continue

        point = points[index]
        point.x = float(np.clip(edge_point[0] / width, 0.0, 1.0))
        point.y = float(np.clip(edge_point[1] / height, 0.0, 1.0))


def _cyclic_chain_indices(
    start: int, end: int, count: int, step: Literal[-1, 1]
) -> list[int]:
    indices = [start]
    current = start
    while current != end and len(indices) <= count:
        current = (current + step) % count
        indices.append(current)
    return indices


def _find_arm_edge_along_normal(
    lab_image: np.ndarray,
    axis_point: np.ndarray,
    direction: np.ndarray,
    axis_length: float,
) -> np.ndarray | None:
    """Find the strongest smoothed colour edge moving outward from the arm axis."""
    height, width = lab_image.shape[:2]
    minimum_distance = max(3, int(round(axis_length * 0.06)))
    maximum_distance = max(
        minimum_distance + 2, int(round(axis_length * 0.42))
    )
    distances = np.arange(minimum_distance, maximum_distance, dtype=float)
    samples = axis_point + distances[:, None] * direction
    valid = (
        (samples[:, 0] >= 1)
        & (samples[:, 0] < width - 1)
        & (samples[:, 1] >= 1)
        & (samples[:, 1] < height - 1)
    )
    samples = samples[valid]
    if len(samples) < 8:
        return None

    pixels = np.rint(samples).astype(int)
    colors = lab_image[pixels[:, 1], pixels[:, 0]].astype(float)
    gradients = np.linalg.norm(np.diff(colors, axis=0), axis=1)
    gradients = np.convolve(gradients, np.ones(7) / 7.0, mode="same")
    edge_index = int(np.argmax(gradients))
    if float(gradients[edge_index]) < 2.5:
        return None

    # Keep the polygon just inside the detected boundary so the overlay remains
    # on visible arm pixels rather than spilling onto clothing or background.
    edge_point = samples[min(edge_index + 1, len(samples) - 1)] - direction * 2.0
    return edge_point


def _align_pads_to_polygon_centerline(
    polygon_points,
    pads,
    shoulder: np.ndarray,
    axis: np.ndarray,
    crop_right_normal: np.ndarray,
    width: int,
    height: int,
) -> None:
    """Follow the irregular polygon centreline without changing proximal/distal level."""
    if len(polygon_points) < 3 or not pads:
        return

    contour = np.array(
        [[point.x * width, point.y * height] for point in polygon_points],
        dtype=np.float32,
    ).reshape((-1, 1, 2))
    axis_squared = float(np.dot(axis, axis))
    axis_length = float(np.sqrt(axis_squared))
    distances = np.linspace(-axis_length * 0.45, axis_length * 0.45, 361)

    for pad in pads:
        current = np.array([pad.x * width, pad.y * height], dtype=float)
        axial_position = float(np.dot(current - shoulder, axis) / axis_squared)
        axis_point = shoulder + axis * axial_position
        samples = axis_point + distances[:, None] * crop_right_normal
        inside = [
            sample
            for sample in samples
            if 0 <= sample[0] < width
            and 0 <= sample[1] < height
            and cv2.pointPolygonTest(
                contour, (float(sample[0]), float(sample[1])), False
            )
            >= 0
        ]
        if len(inside) < 3:
            continue

        center = (inside[0] + inside[-1]) / 2.0
        pad.x = float(np.clip(center[0] / width, 0.0, 1.0))
        pad.y = float(np.clip(center[1] / height, 0.0, 1.0))


def _format_point(point: tuple[float, float]) -> str:
    return f"({point[0]:.3f}, {point[1]:.3f})"


def _arm_error_message(preferred_side: ArmSide | None) -> str:
    if preferred_side:
        return (
            f"Could not detect the same {preferred_side} shoulder, elbow, and wrist "
            "in both images."
        )
    return (
        "Could not detect a clear shoulder, elbow, and wrist. "
        "Ensure one complete arm is visible."
    )
