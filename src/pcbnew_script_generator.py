from __future__ import annotations

from pathlib import Path

from .models import BoardLayout


def build_pcbnew_script(layout: BoardLayout) -> str:
    lines: list[str] = []
    lines.append("import pcbnew")
    lines.append("")
    lines.append("board = pcbnew.GetBoard()")
    lines.append("if board is None:")
    lines.append("    raise RuntimeError('Açık bir PCB board bulunamadı. KiCad içinde .kicad_pcb açıp scripti çalıştırın.')")
    lines.append("")
    lines.append("MM = lambda v: pcbnew.FromMM(float(v))")
    lines.append("")
    lines.append("placements = {")
    for comp in layout.placements:
        lines.append(
            f"    '{comp.ref}': ({comp.x_mm}, {comp.y_mm}, {comp.rotation_deg}),"
        )
    lines.append("}")
    lines.append("")
    lines.append("for ref, (x_mm, y_mm, rot_deg) in placements.items():")
    lines.append("    fp = board.FindFootprintByReference(ref)")
    lines.append("    if fp is None:")
    lines.append("        print(f'[WARN] Footprint bulunamadı: {ref}')")
    lines.append("        continue")
    lines.append("    fp.SetPosition(pcbnew.VECTOR2I(MM(x_mm), MM(y_mm)))")
    lines.append("    fp.SetOrientationDegrees(rot_deg)")
    lines.append("")
    lines.append("net_lookup = board.GetNetInfo().NetsByName()")
    lines.append("")
    lines.append("routes = [")
    for routed in layout.routed_nets:
        for seg in routed.segments:
            lines.append(
                "    ("
                f"'{routed.net_name}', {seg.x1_mm}, {seg.y1_mm}, {seg.x2_mm}, {seg.y2_mm}, '{seg.layer}'"
                "),"
            )
    lines.append("]")
    lines.append("")
    lines.append("for net_name, x1, y1, x2, y2, layer_name in routes:")
    lines.append("    net_item = net_lookup.find(net_name)")
    lines.append("    if net_item is None:")
    lines.append("        print(f'[WARN] Net bulunamadı: {net_name}')")
    lines.append("        continue")
    lines.append("    track = pcbnew.PCB_TRACK(board)")
    lines.append("    track.SetStart(pcbnew.VECTOR2I(MM(x1), MM(y1)))")
    lines.append("    track.SetEnd(pcbnew.VECTOR2I(MM(x2), MM(y2)))")
    lines.append("    if layer_name == 'B.Cu':")
    lines.append("        track.SetLayer(pcbnew.B_Cu)")
    lines.append("    else:")
    lines.append("        track.SetLayer(pcbnew.F_Cu)")
    lines.append("    track.SetWidth(MM(0.25))")
    lines.append("    track.SetNetCode(net_item.GetNet())")
    lines.append("    board.Add(track)")
    lines.append("")
    lines.append("pcbnew.Refresh()")
    lines.append("print('Auto placement/routing script uygulandı.')")

    return "\n".join(lines)


def write_pcbnew_script(layout: BoardLayout, out_file: Path) -> Path:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(build_pcbnew_script(layout), encoding="utf-8")
    return out_file
