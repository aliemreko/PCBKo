from __future__ import annotations

from pathlib import Path
import subprocess

from .models import ProjectSpec


def _project_json(name: str) -> str:
    return (
        '{\n'
        '  "board": {\n'
        '    "3dviewports": [],\n'
        '    "design_settings": {},\n'
        '    "layer_presets": [],\n'
        '    "viewports": []\n'
        '  },\n'
        '  "boards": [],\n'
        '  "cvpcb": {\n'
        '    "equivalence_files": []\n'
        '  },\n'
        '  "libraries": {},\n'
        '  "meta": {\n'
        f'    "filename": "{name}.kicad_pro",\n'
        '    "version": 1\n'
        '  },\n'
        '  "net_settings": {},\n'
        '  "pcbnew": {},\n'
        '  "schematic": {}\n'
        '}\n'
    )


def write_kicad_project_files(spec: ProjectSpec, out_dir: Path) -> dict[str, Path]:
    layout_json = out_dir / "auto_layout.json"
    plan_json = out_dir / "design_plan.json"
    kicad_pcb = out_dir / f"{spec.name}.kicad_pcb"
    kicad_pro = out_dir / f"{spec.name}.kicad_pro"

    if not layout_json.exists():
        raise FileNotFoundError(f"Layout JSON bulunamadı: {layout_json}")
    if not plan_json.exists():
        raise FileNotFoundError(f"Design plan JSON bulunamadı: {plan_json}")

    command = [
        "python3",
        "-m",
        "src.kicad_pcb_worker",
        str(layout_json.resolve()),
        str(plan_json.resolve()),
        str(kicad_pcb.resolve()),
    ]
    workspace_root = Path(__file__).resolve().parent.parent
    process = subprocess.run(
        command,
        cwd=str(workspace_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"KiCad PCB üretimi başarısız:\n{process.stdout}")

    kicad_pro.write_text(_project_json(spec.name), encoding="utf-8")

    return {
        "kicad_pcb": kicad_pcb,
        "kicad_pro": kicad_pro,
    }
