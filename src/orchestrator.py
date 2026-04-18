from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .checks import run_kicad_checks, run_skidl_netlist
from .component_autofill import autofill_components
from .deepseek_agent import DeepSeekPcbAgent
from .kicad_bootstrap import write_kicad_project_files
from .kicad_generator import write_kicad_compatible_outputs
from .layout_outputs import write_layout_outputs
from .models import ProjectSpec
from .plan_normalizer import normalize_plan


console = Console()


def _classify_check(ok: bool, output: str) -> str:
    if ok:
        return "ok"

    lowered = output.lower()
    skip_markers = [
        "kicad-cli bulunamadı",
        "atlandı",
        "kicad_symbol_dir environment variable is missing",
        "can't open file",
    ]
    if any(marker in lowered for marker in skip_markers):
        return "skipped"

    return "failed"


def run_agent_pipeline(
    agent: DeepSeekPcbAgent,
    spec: ProjectSpec,
    out_dir: Path,
    run_checks: bool = False,
) -> dict[str, object]:
    console.print("[cyan]1) Tasarım planı üretiliyor (DeepSeek)...[/cyan]")
    raw_plan = agent.create_design_plan(spec)
    enriched_plan = autofill_components(raw_plan)
    plan = normalize_plan(enriched_plan)

    console.print("[cyan]2) KiCad uyumlu dosyalar yazılıyor...[/cyan]")
    files = write_kicad_compatible_outputs(plan, out_dir)

    console.print("[cyan]3) Otomatik yerleşim ve routing hesaplanıyor...[/cyan]")
    layout_files = write_layout_outputs(plan, spec, out_dir)
    files.update(layout_files)

    console.print("[cyan]4) KiCad proje dosyaları üretiliyor...[/cyan]")
    kicad_files = write_kicad_project_files(spec, out_dir)
    files.update(kicad_files)

    results: dict[str, object] = {
        "plan": plan,
        "files": files,
        "checks": [],
    }

    if run_checks:
        console.print("[cyan]5) SKiDL netlist üretimi test ediliyor...[/cyan]")
        ok, output = run_skidl_netlist(files["skidl"], out_dir)
        results["checks"].append(
            {
                "name": "skidl_netlist",
                "ok": ok,
                "state": _classify_check(ok, output),
                "output": output,
            }
        )

        board_file = out_dir / f"{spec.name}.kicad_pcb"
        if board_file.exists():
            console.print("[cyan]6) KiCad board kontrolü çalıştırılıyor...[/cyan]")
            ok2, output2 = run_kicad_checks(board_file)
            results["checks"].append(
                {
                    "name": "kicad_checks",
                    "ok": ok2,
                    "state": _classify_check(ok2, output2),
                    "output": output2,
                }
            )
        else:
            results["checks"].append(
                {
                    "name": "kicad_checks",
                    "ok": False,
                    "state": "skipped",
                    "output": "kicad_pcb dosyası bulunamadı, KiCad check atlandı.",
                }
            )

    return results
