from __future__ import annotations

import json
from pathlib import Path
import sys

import pcbnew

# ── User-configurable trace width (can be set by GUI) ──────────
_USER_TRACE_WIDTH: float = 0.25

# ── Ensure KiCad 3D model path is set ───────────────────────
import os as _os
_3D_MODEL_DIR = "/usr/share/kicad/3dmodels"
for _var in ("KICAD6_3DMODEL_DIR", "KICAD7_3DMODEL_DIR", "KICAD8_3DMODEL_DIR"):
    if not _os.environ.get(_var):
        _os.environ[_var] = _3D_MODEL_DIR


def mm(value: float) -> int:
    return pcbnew.FromMM(float(value))


def _parse_footprint(footprint: str) -> tuple[str, str] | None:
    if ":" not in footprint:
        return None
    lib, name = footprint.split(":", 1)
    lib = lib.strip()
    name = name.strip()
    if not lib or not name:
        return None
    return lib, name


def _load_footprint(lib: str, name: str):
    try:
        mod = pcbnew.FootprintLoad(lib, name)
        if mod is not None:
            return mod
    except Exception:
        pass

    lib_dir = Path("/usr/share/kicad/footprints") / f"{lib}.pretty"
    if lib_dir.exists():
        try:
            mod = pcbnew.FootprintLoad(str(lib_dir), name)
            if mod is not None:
                return mod
        except Exception:
            pass
    return None


def build_board(layout_path: Path, plan_path: Path, out_pcb: Path) -> None:
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    width = float(payload.get("width_mm", 50.0))
    height = float(payload.get("height_mm", 50.0))
    placements = payload.get("placements", [])
    routed_nets = payload.get("routed_nets", [])
    plan_components = {item.get("ref"): item for item in plan.get("components", [])}
    plan_nets = plan.get("nets", [])

    board = pcbnew.BOARD()

    net_names = {str(net.get("net_name", "")).strip() for net in routed_nets}
    for item in plan_nets:
        name = str(item.get("net_name", "")).strip()
        if name:
            net_names.add(name)

    for net_name in sorted(name for name in net_names if name):
        board.Add(pcbnew.NETINFO_ITEM(board, net_name))

    net_code_by_name: dict[str, int] = {
        name: int(board.GetNetcodeFromNetname(name))
        for name in net_names
        if name
    }

    module_by_ref: dict[str, object] = {}

    corners = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height), (0.0, 0.0)]
    for i in range(len(corners) - 1):
        x1, y1 = corners[i]
        x2, y2 = corners[i + 1]
        edge = pcbnew.PCB_SHAPE(board)
        edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
        edge.SetLayer(pcbnew.Edge_Cuts)
        edge.SetStart(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        edge.SetEnd(pcbnew.VECTOR2I(mm(x2), mm(y2)))
        edge.SetWidth(mm(0.1))
        board.Add(edge)

    for comp in placements:
        x = float(comp.get("x_mm", 0.0))
        y = float(comp.get("y_mm", 0.0))
        rot = float(comp.get("rotation_deg", 0.0))
        ref = str(comp.get("ref", "U?"))
        comp_def = plan_components.get(ref, {})
        footprint = str(comp_def.get("footprint", ""))

        module = None
        parsed = _parse_footprint(footprint)
        if parsed is not None:
            lib, name = parsed
            module = _load_footprint(lib, name)

        if module is not None:
            # Layout positions are component CENTER (centroid of pad bounding box).
            # KiCad SetPosition uses the footprint ANCHOR (usually pin 1).
            # Compute the anchor-to-center offset so we place correctly.
            fp_pads = list(module.Pads())
            cx_off, cy_off = 0.0, 0.0
            if fp_pads:
                pad_xs = [float(pcbnew.ToMM(p.GetPosition().x)) for p in fp_pads]
                pad_ys = [float(pcbnew.ToMM(p.GetPosition().y)) for p in fp_pads]
                cx_off = (min(pad_xs) + max(pad_xs)) / 2.0
                cy_off = (min(pad_ys) + max(pad_ys)) / 2.0

            module.SetReference(ref)
            module.SetValue(str(comp_def.get("value", "")))
            module.SetPosition(pcbnew.VECTOR2I(mm(x - cx_off), mm(y - cy_off)))
            if hasattr(module, "SetOrientationDegrees"):
                module.SetOrientationDegrees(rot)
            board.Add(module)
            module_by_ref[ref] = module
        else:
            text = pcbnew.PCB_TEXT(board)
            text.SetText(ref)
            text.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
            text.SetLayer(pcbnew.F_SilkS)
            text.SetTextHeight(mm(1.0))
            text.SetTextWidth(mm(1.0))
            text.SetTextThickness(mm(0.15))
            board.Add(text)

    for net in plan_nets:
        net_name = str(net.get("net_name", "")).strip()
        if not net_name or net_name not in net_code_by_name:
            continue
        code = net_code_by_name[net_name]
        for node in net.get("nodes", []):
            node_text = str(node)
            if "." not in node_text:
                continue
            ref, pin = node_text.split(".", 1)
            module = module_by_ref.get(ref)
            if module is None:
                continue
            pad = module.FindPadByNumber(pin)
            if pad is None:
                continue
            pad.SetNetCode(code)

    for net in routed_nets:
        net_name = str(net.get("net_name", "")).strip()
        net_code = net_code_by_name.get(net_name, 0)
        for seg in net.get("segments", []):
            x1 = float(seg.get("x1_mm", 0.0))
            y1 = float(seg.get("y1_mm", 0.0))
            x2 = float(seg.get("x2_mm", 0.0))
            y2 = float(seg.get("y2_mm", 0.0))
            layer_name = str(seg.get("layer", "F.Cu"))

            track = pcbnew.PCB_TRACK(board)
            track.SetStart(pcbnew.VECTOR2I(mm(x1), mm(y1)))
            track.SetEnd(pcbnew.VECTOR2I(mm(x2), mm(y2)))
            track.SetWidth(mm(_USER_TRACE_WIDTH))
            track.SetLayer(pcbnew.B_Cu if layer_name == "B.Cu" else pcbnew.F_Cu)
            if net_code > 0:
                track.SetNetCode(net_code)
            board.Add(track)

    out_pcb.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(out_pcb), board)


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: python -m src.kicad_pcb_worker <layout_json> <design_plan_json> <out_kicad_pcb>")
        return 2

    layout_path = Path(sys.argv[1]).resolve()
    plan_path = Path(sys.argv[2]).resolve()
    out_pcb = Path(sys.argv[3]).resolve()

    build_board(layout_path, plan_path, out_pcb)
    print(f"Generated: {out_pcb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
