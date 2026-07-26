from pydantic import BaseModel, Field
from typing import List

class Coordinate(BaseModel):
    x: float = Field(description="Normalized horizontal position in the crop, 0.0 left to 1.0 right")
    y: float = Field(description="Normalized vertical position in the crop, 0.0 top to 1.0 bottom")

class EMSPad(BaseModel):
    label: str
    x: float = Field(description="Normalized horizontal position in the crop, 0.0 left to 1.0 right")
    y: float = Field(description="Normalized vertical position in the crop, 0.0 top to 1.0 bottom")

class MuscleGroup(BaseModel):
    name: str
    polygon_vertices_normalized: List[Coordinate] = Field(
        description="Clockwise boundary points on visible muscle pixels in the flexed arm crop"
    )
    color_hex: str
    ems_pads_normalized: List[EMSPad]

class MuscleAnalysisResult(BaseModel):
    movement_detected: str
    muscles: List[MuscleGroup]


class AccuracyFeedback(BaseModel):
    analysis_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    accurate: bool
