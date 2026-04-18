from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import Settings
from .deepseek_agent import DeepSeekPcbAgent
from .models import ProjectSpec
from .orchestrator import run_agent_pipeline


app = typer.Typer(help="DeepSeek + KiCad AI PCB Agent")
console = Console()


@app.command()
def generate(
    spec: Path = typer.Option(..., help="JSON project spec file"),
    out: Path = typer.Option(Path("output/project"), help="Output directory"),
    run_checks: bool = typer.Option(False, help="Run SKiDL/KiCad checks"),
) -> None:
    if not spec.exists():
        raise typer.BadParameter(f"Spec file not found: {spec}")

    with spec.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    project_spec = ProjectSpec.model_validate(payload)
    settings = Settings.from_env()
    agent = DeepSeekPcbAgent(settings)

    result = run_agent_pipeline(agent, project_spec, out, run_checks=run_checks)

    table = Table(title="AI PCB Agent Çıktıları")
    table.add_column("Tür")
    table.add_column("Dosya")

    files = result["files"]
    labels = {
        "report": "Rapor",
        "json": "Plan JSON",
        "skidl": "SKiDL",
        "layout_json": "Layout JSON",
        "preview_png": "Görsel PNG",
        "pcbnew_script": "KiCad Script",
        "kicad_pcb": "KiCad PCB",
        "kicad_pro": "KiCad Proje",
    }
    for key, path in files.items():
        table.add_row(labels.get(key, key), str(path))
    console.print(table)

    checks = result["checks"]
    if checks:
        check_table = Table(title="Kontroller")
        check_table.add_column("Adım")
        check_table.add_column("Durum")
        for item in checks:
            state = str(item.get("state", "ok" if item.get("ok") else "failed")).upper()
            status = state
            check_table.add_row(item["name"], status)
        console.print(check_table)


if __name__ == "__main__":
    app()
