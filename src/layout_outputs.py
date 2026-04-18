from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .layout_router import generate_board_layout, route_with_fixed_placements
from .models import BoardLayout, ComponentPlacement, DesignPlan, ProjectSpec
from .pcbnew_script_generator import write_pcbnew_script
from .visualizer import save_board_preview


def _write_layout(layout: BoardLayout, out_dir: Path) -> dict[str, Path]:
    """Shared helper: serialise a BoardLayout to disk."""
    layout_json = out_dir / "auto_layout.json"
    preview_png = out_dir / "board_preview.png"
    pcbnew_script = out_dir / "apply_layout_in_kicad.py"

    layout_json.write_text(layout.model_dump_json(indent=2), encoding="utf-8")
    save_board_preview(layout, preview_png)
    write_pcbnew_script(layout, pcbnew_script)

    return {
        "layout_json": layout_json,
        "preview_png": preview_png,
        "pcbnew_script": pcbnew_script,
    }


def write_layout_outputs(
    plan: DesignPlan, spec: ProjectSpec, out_dir: Path,
) -> dict[str, Path]:
    """Auto-place **and** route, then write outputs."""
    layout = generate_board_layout(plan, spec)
    return _write_layout(layout, out_dir)


def write_layout_outputs_with_placements(
    plan: DesignPlan,
    placements: list[ComponentPlacement],
    board_w: float,
    board_h: float,
    out_dir: Path,
) -> tuple[BoardLayout, dict[str, Path]]:
    """Route with *fixed* user-provided placements and write outputs.

    Returns both the layout object (so the caller can display routed
    traces on the canvas) and the dict of written file paths.
    """
    layout = route_with_fixed_placements(plan, placements, board_w, board_h)
    files = _write_layout(layout, out_dir)
    return layout, files
