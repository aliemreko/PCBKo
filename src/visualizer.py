from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .models import BoardLayout


def save_board_preview(layout: BoardLayout, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 7), dpi=160)

    board = Rectangle((0, 0), layout.width_mm, layout.height_mm, fill=False, linewidth=2.0)
    ax.add_patch(board)

    for comp in layout.placements:
        x = comp.x_mm - comp.width_mm / 2
        y = comp.y_mm - comp.height_mm / 2
        rect = Rectangle((x, y), comp.width_mm, comp.height_mm, fill=True, alpha=0.25)
        ax.add_patch(rect)
        ax.text(comp.x_mm, comp.y_mm, comp.ref, fontsize=7, ha="center", va="center")

    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf"]
    for i, routed in enumerate(layout.routed_nets):
        color = colors[i % len(colors)]
        for seg in routed.segments:
            ax.plot([seg.x1_mm, seg.x2_mm], [seg.y1_mm, seg.y2_mm], color=color, linewidth=1.4)

    ax.set_title("AI PCB Auto Placement + Routing Preview")
    ax.set_xlabel("mm")
    ax.set_ylabel("mm")
    ax.set_xlim(0, layout.width_mm)
    ax.set_ylim(0, layout.height_mm)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linewidth=0.2, alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_file)
    plt.close(fig)
