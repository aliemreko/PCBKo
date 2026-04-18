from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys


def run_skidl_netlist(skidl_script: Path, work_dir: Path) -> tuple[bool, str]:
    script_path = skidl_script.resolve()
    command = [sys.executable, str(script_path)]
    env = os.environ.copy()

    symbol_dir = Path("/usr/share/kicad/symbols")
    if symbol_dir.exists():
        value = str(symbol_dir)
        env.setdefault("KICAD_SYMBOL_DIR", value)
        env.setdefault("KICAD6_SYMBOL_DIR", value)
        env.setdefault("KICAD7_SYMBOL_DIR", value)
        env.setdefault("KICAD8_SYMBOL_DIR", value)
        env.setdefault("KICAD9_SYMBOL_DIR", value)

    process = subprocess.run(
        command,
        cwd=str(work_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return process.returncode == 0, process.stdout


def run_kicad_checks(project_file: Path) -> tuple[bool, str]:
    if shutil.which("kicad-cli") is None:
        return False, "kicad-cli bulunamadı; KiCad check adımı atlandı."

    pcb_file = project_file

    help_process = subprocess.run(
        ["kicad-cli", "pcb", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    help_text = help_process.stdout.lower()

    if "drc" in help_text:
        drc = subprocess.run(
            ["kicad-cli", "pcb", "drc", str(pcb_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return drc.returncode == 0, f"[DRC]\n{drc.stdout}"

    sanity = subprocess.run(
        [
            "python3",
            "-c",
            (
                "import pcbnew,sys;"
                "b=pcbnew.LoadBoard(sys.argv[1]);"
                "tracks=b.GetTracks();draws=b.GetDrawings();"
                "tc=tracks.GetCount() if hasattr(tracks,'GetCount') else len(list(tracks));"
                "dc=draws.GetCount() if hasattr(draws,'GetCount') else len(list(draws));"
                "print(f'TRACKS={tc} DRAWINGS={dc}')"
            ),
            str(pcb_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if sanity.returncode != 0:
        return False, f"KiCad board yükleme hatası:\n{sanity.stdout}"

    return True, f"kicad-cli sürümünde DRC komutu yok; board sanity check başarılı.\n{sanity.stdout}"
