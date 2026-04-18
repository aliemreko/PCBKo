from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectSpec(BaseModel):
    name: str
    description: str
    constraints: list[str] = Field(default_factory=list)
    io_requirements: list[str] = Field(default_factory=list)
    preferred_parts: list[str] = Field(default_factory=list)
    board_outline: str = "50mm x 50mm"
    layer_count: int = 2


class Component(BaseModel):
    ref: str
    value: str
    footprint: str = ""
    symbol: str = ""
    notes: str = ""


class NetConnection(BaseModel):
    net_name: str
    nodes: list[str]


class PlacementHint(BaseModel):
    target: str
    hint: str


class DesignPlan(BaseModel):
    title: str
    assumptions: list[str] = Field(default_factory=list)
    components: list[Component]
    nets: list[NetConnection]
    placement_hints: list[PlacementHint] = Field(default_factory=list)
    design_checks: list[str] = Field(default_factory=list)


class ComponentPlacement(BaseModel):
    ref: str
    x_mm: float
    y_mm: float
    rotation_deg: float = 0.0
    width_mm: float
    height_mm: float


class TraceSegment(BaseModel):
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    layer: str = "F.Cu"


class RoutedNet(BaseModel):
    net_name: str
    nodes: list[str] = Field(default_factory=list)
    segments: list[TraceSegment] = Field(default_factory=list)


class BoardLayout(BaseModel):
    width_mm: float
    height_mm: float
    placements: list[ComponentPlacement]
    routed_nets: list[RoutedNet]
    metrics: dict[str, float] = Field(default_factory=dict)
