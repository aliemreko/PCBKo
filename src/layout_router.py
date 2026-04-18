from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import heapq
import math
from pathlib import Path
import random
import re

try:
    import pcbnew  # type: ignore
except Exception:  # pragma: no cover
    pcbnew = None

from .models import (
    BoardLayout,
    ComponentPlacement,
    DesignPlan,
    NetConnection,
    ProjectSpec,
    RoutedNet,
    TraceSegment,
)

# ── User-configurable safe-zone / clearance parameters ──────────
# These can be updated by the GUI before calling generate_board_layout().
_USER_COMP_SPACING: float = 5.0        # mm gap between component bounding boxes
_USER_TRACE_CLEARANCE: float = 0.25    # mm clearance between traces of different nets
_USER_TRACE_WIDTH: float = 0.25        # mm trace width (forwarded to KiCad workers)
_USER_INFLATE_RADIUS: int = 1          # routing grid cells of isolation around foreign traces
_USER_BOARD_AREA_MULT: float = 3.0     # board area = comp_area * this multiplier


@dataclass(frozen=True)
class _Cell:
    x: int
    y: int


_FOOTPRINT_CACHE: dict[str, tuple[tuple[float, float], dict[str, tuple[float, float]]]] = {}

# ── Known footprint geometry: CENTER-RELATIVE pin offsets ─────────────────
# Size = (width, height) of pad bounding box + margin
# Pin offsets = relative to centroid of all pads
_KNOWN_FOOTPRINT_DB: dict[str, tuple[tuple[float, float], dict[str, tuple[float, float]]]] = {
    # DIP-8 (7.62mm row spacing, 2.54mm pitch, 4 pins per side)
    "Package_DIP:DIP-8_W7.62mm": (
        (9.62, 9.62),
        {"1": (-3.81, -3.81), "2": (-3.81, -1.27), "3": (-3.81, 1.27), "4": (-3.81, 3.81),
         "5": (3.81, 3.81), "6": (3.81, 1.27), "7": (3.81, -1.27), "8": (3.81, -3.81)},
    ),
    # TO-92 Inline (1.27mm pitch, 3 pins)
    "Package_TO_SOT_THT:TO-92_Inline": (
        (4.54, 2.0),
        {"1": (-1.27, 0.0), "2": (0.0, 0.0), "3": (1.27, 0.0)},
    ),
    # Axial resistor (7.62mm pad spacing)
    "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal": (
        (9.62, 2.0),
        {"1": (-3.81, 0.0), "2": (3.81, 0.0)},
    ),
    # Radial electrolytic cap (2mm pad spacing)
    "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm": (
        (4.0, 2.0),
        {"1": (-1.0, 0.0), "2": (1.0, 0.0)},
    ),
    # Disc ceramic cap (5mm pad spacing)
    "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm": (
        (7.0, 2.0),
        {"1": (-2.5, 0.0), "2": (2.5, 0.0)},
    ),
    # 5mm LED (2.54mm pad spacing)
    "LED_THT:LED_D5.0mm": (
        (4.54, 2.0),
        {"1": (-1.27, 0.0), "2": (1.27, 0.0)},
    ),
    # 1x3 pin header (2.54mm pitch, vertical row)
    "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical": (
        (2.0, 7.08),
        {"1": (0.0, -2.54), "2": (0.0, 0.0), "3": (0.0, 2.54)},
    ),
    # Common 2-pin pin headers
    "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical": (
        (2.0, 4.54),
        {"1": (0.0, -1.27), "2": (0.0, 1.27)},
    ),
    # SMD passives
    "Resistor_SMD:R_0603_1608Metric": ((2.6, 1.6), {"1": (-0.8, 0.0), "2": (0.8, 0.0)}),
    "Resistor_SMD:R_0805_2012Metric": ((3.0, 2.0), {"1": (-1.0, 0.0), "2": (1.0, 0.0)}),
    "Capacitor_SMD:C_0603_1608Metric": ((2.6, 1.6), {"1": (-0.8, 0.0), "2": (0.8, 0.0)}),
    "Capacitor_SMD:C_0805_2012Metric": ((3.0, 2.0), {"1": (-1.0, 0.0), "2": (1.0, 0.0)}),
    # DIP-14 / DIP-16
    "Package_DIP:DIP-14_W7.62mm": (
        (9.62, 17.78),
        {str(i+1): (-3.81, -7.62 + i*2.54) for i in range(7)} |
        {str(14-i): (3.81, -7.62 + i*2.54) for i in range(7)},
    ),
    "Package_DIP:DIP-16_W7.62mm": (
        (9.62, 20.32),
        {str(i+1): (-3.81, -8.89 + i*2.54) for i in range(8)} |
        {str(16-i): (3.81, -8.89 + i*2.54) for i in range(8)},
    ),
    # ── ESP32-C3-WROOM-02 module (18 signal pins + GND pad) ────────
    "RF_Module:ESP32-C3-WROOM-02": (
        (19.50, 14.00),
        {"1": (-8.75, -6.00), "2": (-8.75, -4.50), "3": (-8.75, -3.00),
         "4": (-8.75, -1.50), "5": (-8.75, 0.00), "6": (-8.75, 1.50),
         "7": (-8.75, 3.00), "8": (-8.75, 4.50), "9": (-8.75, 6.00),
         "10": (8.75, 6.00), "11": (8.75, 4.50), "12": (8.75, 3.00),
         "13": (8.75, 1.50), "14": (8.75, 0.00), "15": (8.75, -1.50),
         "16": (8.75, -3.00), "17": (8.75, -4.50), "18": (8.75, -6.00),
         "19": (0.0, 0.0)},
    ),
    # ── DHT11/DHT22 sensor (Aosong 4-pin THT, vertical row) ───────
    "Sensor:Aosong_DHT11_5.5x12.0_P2.54mm": (
        (4.0, 9.62),
        {"1": (0.0, -3.81), "2": (0.0, -1.27), "3": (0.0, 1.27), "4": (0.0, 3.81)},
    ),
    # 1x4 pin header (2.54mm pitch, vertical row)
    "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical": (
        (2.0, 9.62),
        {"1": (0.0, -3.81), "2": (0.0, -1.27), "3": (0.0, 1.27), "4": (0.0, 3.81)},
    ),
    # 1x5 pin header
    "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical": (
        (2.0, 12.16),
        {"1": (0.0, -5.08), "2": (0.0, -2.54), "3": (0.0, 0.0),
         "4": (0.0, 2.54), "5": (0.0, 5.08)},
    ),
    # 1x6 pin header
    "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical": (
        (2.0, 14.70),
        {"1": (0.0, -6.35), "2": (0.0, -3.81), "3": (0.0, -1.27),
         "4": (0.0, 1.27), "5": (0.0, 3.81), "6": (0.0, 6.35)},
    ),
}


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
    if pcbnew is None:
        return None
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


def _footprint_geometry(footprint: str) -> tuple[tuple[float, float], dict[str, tuple[float, float]]]:
    """Return (size, center_relative_pin_offsets) for a footprint.

    All pin offsets are relative to the centroid of the pad bounding box,
    so that placement (x, y) represents the component centre.
    """
    if footprint in _FOOTPRINT_CACHE:
        return _FOOTPRINT_CACHE[footprint]

    # 1) Check known footprint database (always center-relative)
    if footprint in _KNOWN_FOOTPRINT_DB:
        result = _KNOWN_FOOTPRINT_DB[footprint]
        _FOOTPRINT_CACHE[footprint] = result
        return result

    # Partial-name match for common families
    fp_name = footprint.split(":", 1)[-1] if ":" in footprint else footprint
    for known_fp, known_data in _KNOWN_FOOTPRINT_DB.items():
        if fp_name in known_fp or known_fp.endswith(fp_name):
            _FOOTPRINT_CACHE[footprint] = known_data
            return known_data

    # 2) Auto-discovery: use footprint_finder to load geometry from KiCad libs
    try:
        from .footprint_finder import get_footprint_geometry as _auto_geom
        auto = _auto_geom(footprint)
        if auto is not None:
            _FOOTPRINT_CACHE[footprint] = auto
            return auto
    except Exception:
        pass

    # 3) Try loading via pcbnew and normalise to center-relative
    fallback = ((6.0, 4.0), {})
    fp_parts = _parse_footprint(footprint)
    if pcbnew is None or fp_parts is None:
        _FOOTPRINT_CACHE[footprint] = fallback
        return fallback

    lib, name = fp_parts
    try:
        mod = _load_footprint(lib, name)
        if mod is None:
            _FOOTPRINT_CACHE[footprint] = fallback
            return fallback

        pads = list(mod.Pads())
        if not pads:
            _FOOTPRINT_CACHE[footprint] = fallback
            return fallback

        xs: list[float] = []
        ys: list[float] = []
        raw_pins: dict[str, tuple[float, float]] = {}

        for pad in pads:
            pos = pad.GetPosition()
            x_mm = float(pcbnew.ToMM(pos.x))
            y_mm = float(pcbnew.ToMM(pos.y))
            xs.append(x_mm)
            ys.append(y_mm)
            raw_pins[str(pad.GetPadName())] = (x_mm, y_mm)

        # Normalise to center-relative
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        pin_offsets: dict[str, tuple[float, float]] = {
            name: (round(px - cx, 3), round(py - cy, 3))
            for name, (px, py) in raw_pins.items()
        }

        width = max(2.0, (max(xs) - min(xs)) + 2.0)
        height = max(2.0, (max(ys) - min(ys)) + 2.0)

        result = ((width, height), pin_offsets)
        _FOOTPRINT_CACHE[footprint] = result
        return result
    except Exception:
        _FOOTPRINT_CACHE[footprint] = fallback
        return fallback


def _parse_outline_mm(outline: str) -> tuple[float, float]:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mm\s*x\s*([0-9]+(?:\.[0-9]+)?)\s*mm", outline.lower())
    if not match:
        return 50.0, 50.0
    return float(match.group(1)), float(match.group(2))


def _component_size(ref: str, footprint: str) -> tuple[float, float]:
    geo_size, _ = _footprint_geometry(footprint)
    if geo_size != (6.0, 4.0):
        return geo_size

    prefix = (ref[:1] or "").upper()
    if prefix == "U":
        return 9.0, 9.0
    if prefix == "J":
        return 8.0, 4.0
    if prefix == "Q":
        return 5.0, 5.0
    if prefix == "D":
        return 5.0, 3.0
    return 5.0, 2.5


def _extract_refs(nodes: list[str]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if "." not in node:
            continue
        ref = node.split(".", 1)[0]
        if ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def _build_edge_weights(plan: DesignPlan) -> dict[tuple[str, str], float]:
    weights: dict[tuple[str, str], float] = defaultdict(float)
    for net in plan.nets:
        refs = _extract_refs(net.nodes)
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a, b = sorted((refs[i], refs[j]))
                weights[(a, b)] += 1.0
    return weights


def _pin_offset(pin: str) -> tuple[float, float]:
    seed = sum(ord(ch) for ch in pin)
    angle = (seed % 360) * math.pi / 180.0
    radius = 1.5
    return math.cos(angle) * radius, math.sin(angle) * radius


def _pin_point(
    node: str,
    placement_map: dict[str, ComponentPlacement],
    pin_offset_map: dict[str, dict[str, tuple[float, float]]],
) -> tuple[float, float] | None:
    if "." not in node:
        return None
    ref, pin = node.split(".", 1)
    comp = placement_map.get(ref)
    if comp is None:
        return None

    ref_offsets = pin_offset_map.get(ref, {})
    if pin in ref_offsets:
        ox, oy = ref_offsets[pin]
        return comp.x_mm + ox, comp.y_mm + oy

    dx, dy = _pin_offset(pin)
    sx = 1.0 if dx >= 0 else -1.0
    sy = 1.0 if dy >= 0 else -1.0
    dx = sx * (comp.width_mm / 2 + 0.8)
    dy = sy * (comp.height_mm / 2 + 0.8)
    return comp.x_mm + dx, comp.y_mm + dy


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _layout_components(
    plan: DesignPlan,
    width_mm: float,
    height_mm: float,
    seed: int = 42,
) -> list[ComponentPlacement]:
    rng = random.Random(seed)
    refs = [comp.ref for comp in plan.components]
    component_map = {comp.ref: comp for comp in plan.components}
    sizes = {
        ref: _component_size(ref, component_map[ref].footprint)
        for ref in refs
    }
    weights = _build_edge_weights(plan)

    pos: dict[str, tuple[float, float]] = {}
    for ref in refs:
        margin = 4.0
        pos[ref] = (
            rng.uniform(margin, width_mm - margin),
            rng.uniform(margin, height_mm - margin),
        )

    for _ in range(400):
        forces: dict[str, tuple[float, float]] = {ref: (0.0, 0.0) for ref in refs}

        for i, ra in enumerate(refs):
            xa, ya = pos[ra]
            for rb in refs[i + 1 :]:
                xb, yb = pos[rb]
                dx = xa - xb
                dy = ya - yb
                dist2 = dx * dx + dy * dy + 1e-6
                dist = math.sqrt(dist2)
                rep = 50.0 / dist2
                fx = rep * dx / dist
                fy = rep * dy / dist
                fa = forces[ra]
                fb = forces[rb]
                forces[ra] = (fa[0] + fx, fa[1] + fy)
                forces[rb] = (fb[0] - fx, fb[1] - fy)

        for (a, b), weight in weights.items():
            xa, ya = pos[a]
            xb, yb = pos[b]
            dx = xb - xa
            dy = yb - ya
            dist = math.sqrt(dx * dx + dy * dy) + 1e-6
            # Use size-adaptive target distance
            wa, ha = sizes[a]
            wb, hb = sizes[b]
            target = (wa + wb) / 2 + 4.0
            spring_k = 0.04 * weight
            pull = spring_k * (dist - target)
            fx = pull * dx / dist
            fy = pull * dy / dist
            fa = forces[a]
            fb = forces[b]
            forces[a] = (fa[0] + fx, fa[1] + fy)
            forces[b] = (fb[0] - fx, fb[1] - fy)

        for ref in refs:
            x, y = pos[ref]
            fx, fy = forces[ref]
            w, h = sizes[ref]
            margin_x = w / 2 + 2.0
            margin_y = h / 2 + 2.0
            nx = _clamp(x + fx * 0.12, margin_x, width_mm - margin_x)
            ny = _clamp(y + fy * 0.12, margin_y, height_mm - margin_y)
            pos[ref] = (nx, ny)

        for i, ra in enumerate(refs):
            xa, ya = pos[ra]
            wa, ha = sizes[ra]
            for rb in refs[i + 1 :]:
                xb, yb = pos[rb]
                wb, hb = sizes[rb]
                min_dx = (wa + wb) / 2 + _USER_COMP_SPACING
                min_dy = (ha + hb) / 2 + _USER_COMP_SPACING
                dx = xb - xa
                dy = yb - ya
                overlap_x = min_dx - abs(dx)
                overlap_y = min_dy - abs(dy)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue

                push_x = overlap_x / 2
                push_y = overlap_y / 2
                if dx >= 0:
                    xa -= push_x
                    xb += push_x
                else:
                    xa += push_x
                    xb -= push_x
                if dy >= 0:
                    ya -= push_y
                    yb += push_y
                else:
                    ya += push_y
                    yb -= push_y

                ma_x = wa / 2 + 2.0
                ma_y = ha / 2 + 2.0
                mb_x = wb / 2 + 2.0
                mb_y = hb / 2 + 2.0
                xa = _clamp(xa, ma_x, width_mm - ma_x)
                ya = _clamp(ya, ma_y, height_mm - ma_y)
                xb = _clamp(xb, mb_x, width_mm - mb_x)
                yb = _clamp(yb, mb_y, height_mm - mb_y)
                pos[ra] = (xa, ya)
                pos[rb] = (xb, yb)

    edge_list = [(a, b, w) for (a, b), w in weights.items()]

    def wire_cost(current: dict[str, tuple[float, float]]) -> float:
        total = 0.0
        for a, b, w in edge_list:
            ax, ay = current[a]
            bx, by = current[b]
            total += math.dist((ax, ay), (bx, by)) * w

        for i, ra in enumerate(refs):
            xa, ya = current[ra]
            wa, ha = sizes[ra]
            for rb in refs[i + 1 :]:
                xb, yb = current[rb]
                wb, hb = sizes[rb]
                dx = xa - xb
                dy = ya - yb
                dist = math.hypot(dx, dy) + 1e-6
                total += 120.0 / dist

                min_dx = (wa + wb) / 2 + _USER_COMP_SPACING
                min_dy = (ha + hb) / 2 + _USER_COMP_SPACING
                overlap_x = min_dx - abs(dx)
                overlap_y = min_dy - abs(dy)
                if overlap_x > 0 and overlap_y > 0:
                    total += 10000.0 + overlap_x * overlap_y * 1500.0

        return total

    best_cost = wire_cost(pos)
    temp = 3.0
    for _ in range(1500):
        ref = refs[rng.randrange(len(refs))]
        x, y = pos[ref]
        w, h = sizes[ref]
        margin_x = w / 2 + 2.0
        margin_y = h / 2 + 2.0
        nx = _clamp(x + rng.uniform(-4.0, 4.0), margin_x, width_mm - margin_x)
        ny = _clamp(y + rng.uniform(-4.0, 4.0), margin_y, height_mm - margin_y)

        pos[ref] = (nx, ny)
        cand_cost = wire_cost(pos)
        delta = cand_cost - best_cost
        accept = delta <= 0 or rng.random() < math.exp(-delta / max(0.05, temp))
        if accept:
            best_cost = cand_cost
        else:
            pos[ref] = (x, y)
        temp *= 0.996

    # Snap positions to 1.27mm grid (standard KiCad grid)
    GRID = 1.27
    placements: list[ComponentPlacement] = []
    for ref in refs:
        w, h = sizes[ref]
        x, y = pos[ref]
        gx = round(round(x / GRID) * GRID, 3)
        gy = round(round(y / GRID) * GRID, 3)
        placements.append(
            ComponentPlacement(
                ref=ref,
                x_mm=gx,
                y_mm=gy,
                width_mm=w,
                height_mm=h,
                rotation_deg=0.0,
            )
        )
    return placements


def _to_cell(x_mm: float, y_mm: float, step_mm: float) -> _Cell:
    return _Cell(int(round(x_mm / step_mm)), int(round(y_mm / step_mm)))


def _to_mm(cell: _Cell, step_mm: float) -> tuple[float, float]:
    return cell.x * step_mm, cell.y * step_mm


def _blocked_cells(
    placements: list[ComponentPlacement],
    width_mm: float,
    height_mm: float,
    step_mm: float,
) -> set[_Cell]:
    """Hard-blocked cells: core of component bodies (shrunk by 0.3mm
    from each edge so that pins on the perimeter are NOT blocked)."""
    blocked: set[_Cell] = set()
    max_x = int(round(width_mm / step_mm))
    max_y = int(round(height_mm / step_mm))

    for comp in placements:
        shrink = 0.8  # shrink inward so perimeter pins stay outside
        half_w = comp.width_mm / 2 - shrink
        half_h = comp.height_mm / 2 - shrink
        if half_w < 0.3 or half_h < 0.3:
            continue  # component too small to block interior
        x1 = max(0.0, comp.x_mm - half_w)
        x2 = min(width_mm, comp.x_mm + half_w)
        y1 = max(0.0, comp.y_mm - half_h)
        y2 = min(height_mm, comp.y_mm + half_h)

        cx1 = int(math.floor(x1 / step_mm))
        cx2 = int(math.ceil(x2 / step_mm))
        cy1 = int(math.floor(y1 / step_mm))
        cy2 = int(math.ceil(y2 / step_mm))

        for cx in range(cx1, cx2 + 1):
            for cy in range(cy1, cy2 + 1):
                if 0 <= cx <= max_x and 0 <= cy <= max_y:
                    blocked.add(_Cell(cx, cy))

    return blocked


def _component_penalty_cells(
    placements: list[ComponentPlacement],
    width_mm: float,
    height_mm: float,
    step_mm: float,
) -> dict[_Cell, float]:
    """Cells within or near component bodies get a heavy routing penalty
    so traces strongly prefer to go around components."""
    penalties: dict[_Cell, float] = {}
    max_x = int(round(width_mm / step_mm))
    max_y = int(round(height_mm / step_mm))

    for comp in placements:
        margin = 1.0  # penalise area slightly larger than body
        x1 = max(0.0, comp.x_mm - comp.width_mm / 2 - margin)
        x2 = min(width_mm, comp.x_mm + comp.width_mm / 2 + margin)
        y1 = max(0.0, comp.y_mm - comp.height_mm / 2 - margin)
        y2 = min(height_mm, comp.y_mm + comp.height_mm / 2 + margin)

        cx1 = int(math.floor(x1 / step_mm))
        cx2 = int(math.ceil(x2 / step_mm))
        cy1 = int(math.floor(y1 / step_mm))
        cy2 = int(math.ceil(y2 / step_mm))

        for cx in range(cx1, cx2 + 1):
            for cy in range(cy1, cy2 + 1):
                if 0 <= cx <= max_x and 0 <= cy <= max_y:
                    cell = _Cell(cx, cy)
                    penalties[cell] = penalties.get(cell, 0.0) + 25.0

    return penalties


def _astar_route(
    start: _Cell,
    goal: _Cell,
    blocked: set[_Cell],
    width_mm: float,
    height_mm: float,
    step_mm: float,
    extra_cost: dict[_Cell, float],
    preferred_y: int | None = None,
    band_weight: float = 0.0,
) -> list[_Cell] | None:
    max_x = int(round(width_mm / step_mm))
    max_y = int(round(height_mm / step_mm))

    def in_bounds(cell: _Cell) -> bool:
        return 0 <= cell.x <= max_x and 0 <= cell.y <= max_y

    def h(cell: _Cell) -> float:
        return abs(cell.x - goal.x) + abs(cell.y - goal.y)

    blocked_local = set(blocked)
    blocked_local.discard(start)
    blocked_local.discard(goal)

    # State = (cell, direction_index)  where dir_index: 0=H, 1=V, -1=start
    TURN_PENALTY = 2.0
    DIRS = [
        (_Cell(1, 0), 0), (_Cell(-1, 0), 0),  # horizontal
        (_Cell(0, 1), 1), (_Cell(0, -1), 1),   # vertical
    ]

    frontier: list[tuple[float, int, _Cell, int]] = []
    heapq.heappush(frontier, (h(start), 0, start, -1))
    came_from: dict[tuple[int, int, int], tuple[int, int, int] | None] = {
        (start.x, start.y, -1): None
    }
    cost_so_far: dict[tuple[int, int, int], float] = {
        (start.x, start.y, -1): 0.0
    }
    sequence = 0

    goal_state: tuple[int, int, int] | None = None

    while frontier:
        _, _, current, cur_dir = heapq.heappop(frontier)
        cur_key = (current.x, current.y, cur_dir)

        if current == goal:
            goal_state = cur_key
            break

        for delta, nxt_dir in DIRS:
            nxt = _Cell(current.x + delta.x, current.y + delta.y)
            if not in_bounds(nxt) or nxt in blocked_local:
                continue
            step_cost = 1.0 + extra_cost.get(nxt, 0.0)
            # Penalize direction changes to get cleaner routes
            if cur_dir >= 0 and nxt_dir != cur_dir:
                step_cost += TURN_PENALTY
            if preferred_y is not None and band_weight > 0:
                step_cost += band_weight * abs(nxt.y - preferred_y)
            new_cost = cost_so_far[cur_key] + step_cost
            nxt_key = (nxt.x, nxt.y, nxt_dir)
            if nxt_key not in cost_so_far or new_cost < cost_so_far[nxt_key]:
                cost_so_far[nxt_key] = new_cost
                came_from[nxt_key] = cur_key
                sequence += 1
                priority = new_cost + h(nxt)
                heapq.heappush(frontier, (priority, sequence, nxt, nxt_dir))

    if goal_state is None:
        return None

    path: list[_Cell] = []
    cursor: tuple[int, int, int] | None = goal_state
    while cursor is not None:
        path.append(_Cell(cursor[0], cursor[1]))
        cursor = came_from.get(cursor)
    path.reverse()
    return path


def _inflate_cells(cells: set[_Cell], radius: int) -> set[_Cell]:
    if radius <= 0:
        return set(cells)
    result: set[_Cell] = set(cells)
    for cell in cells:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) + abs(dy) > radius:
                    continue
                result.add(_Cell(cell.x + dx, cell.y + dy))
    return result


def _path_to_segments(
    path: list[_Cell],
    step_mm: float,
    start_exact: tuple[float, float] | None = None,
    end_exact: tuple[float, float] | None = None,
) -> list[TraceSegment]:
    """Convert cell path to trace segments, with exact pin endpoint snapping via L-shaped stubs."""
    if len(path) < 2:
        return []

    points: list[tuple[float, float]] = [_to_mm(cell, step_mm) for cell in path]

    # Add L-shaped stub from exact pin position to first grid point
    if start_exact is not None:
        sx, sy = round(start_exact[0], 3), round(start_exact[1], 3)
        gx, gy = points[0]
        if abs(sx - gx) > 0.01 or abs(sy - gy) > 0.01:
            # L-shape: exact -> (gx, sy) -> grid
            points.insert(0, (gx, sy))
            points.insert(0, (sx, sy))

    # Add L-shaped stub from last grid point to exact pin position
    if end_exact is not None:
        ex, ey = round(end_exact[0], 3), round(end_exact[1], 3)
        gx, gy = points[-1]
        if abs(ex - gx) > 0.01 or abs(ey - gy) > 0.01:
            # L-shape: grid -> (ex, gy) -> exact
            points.append((ex, gy))
            points.append((ex, ey))

    # Build segments by merging collinear runs
    segments: list[TraceSegment] = []
    sx, sy = points[0]
    px, py = points[0]
    dx_prev: float | None = None
    dy_prev: float | None = None

    for i in range(1, len(points)):
        cx, cy = points[i]
        dx = cx - px
        dy = cy - py
        if dx_prev is None:
            dx_prev, dy_prev = dx, dy
        elif not _collinear(dx_prev, dy_prev, dx, dy):
            segments.append(
                TraceSegment(
                    x1_mm=round(sx, 3), y1_mm=round(sy, 3),
                    x2_mm=round(px, 3), y2_mm=round(py, 3),
                )
            )
            sx, sy = px, py
            dx_prev, dy_prev = dx, dy
        else:
            dx_prev, dy_prev = cx - sx, cy - sy
        px, py = cx, cy

    segments.append(
        TraceSegment(
            x1_mm=round(sx, 3), y1_mm=round(sy, 3),
            x2_mm=round(px, 3), y2_mm=round(py, 3),
        )
    )

    # Remove zero-length segments
    segments = [s for s in segments if not (s.x1_mm == s.x2_mm and s.y1_mm == s.y2_mm)]
    return segments


def _collinear(dx1: float, dy1: float, dx2: float, dy2: float) -> bool:
    """Check if two direction vectors are collinear (same line)."""
    # Both horizontal
    if abs(dy1) < 1e-6 and abs(dy2) < 1e-6:
        return True
    # Both vertical
    if abs(dx1) < 1e-6 and abs(dx2) < 1e-6:
        return True
    return False


def _route_nets(
    plan: DesignPlan,
    placements: list[ComponentPlacement],
    width_mm: float,
    height_mm: float,
    pin_offset_override: dict[str, dict[str, tuple[float, float]]] | None = None,
) -> list[RoutedNet]:
    def line_cells(a: _Cell, b: _Cell) -> list[_Cell]:
        cells: list[_Cell] = []
        if a.x == b.x:
            step = 1 if b.y >= a.y else -1
            for y in range(a.y, b.y + step, step):
                cells.append(_Cell(a.x, y))
            return cells
        if a.y == b.y:
            step = 1 if b.x >= a.x else -1
            for x in range(a.x, b.x + step, step):
                cells.append(_Cell(x, a.y))
            return cells
        return [a, b]

    def choose_l_fallback(
        start: _Cell,
        goal: _Cell,
        blocked: set[_Cell],
        foreign_block: set[_Cell],
    ) -> list[_Cell] | None:
        elbow1 = _Cell(goal.x, start.y)
        elbow2 = _Cell(start.x, goal.y)

        def candidate(a: _Cell, e: _Cell, b: _Cell) -> tuple[float, list[_Cell]]:
            first = line_cells(a, e)
            second = line_cells(e, b)
            path = first + second[1:]
            blocked_hits = sum(1 for c in path if c in blocked)
            foreign_hits = sum(1 for c in path if c in foreign_block)
            length = len(path)
            score = blocked_hits * 1000.0 + foreign_hits * 250.0 + length
            return score, path

        score1, path1 = candidate(start, elbow1, goal)
        score2, path2 = candidate(start, elbow2, goal)
        best_score, best_path = (score1, path1) if score1 <= score2 else (score2, path2)
        if best_score >= 1000.0:
            return None
        return best_path

    def nearest_cell(source: _Cell, targets: set[_Cell]) -> _Cell:
        return min(targets, key=lambda t: abs(t.x - source.x) + abs(t.y - source.y))

    def route_net_on_layer(
        net: NetConnection,
        pin_points: list[tuple[str, tuple[float, float]]],
        global_owner: dict[_Cell, str],
        global_penalty: dict[_Cell, float],
        component_blocked: set[_Cell],
        preferred_y: int,
    ) -> tuple[list[TraceSegment], set[_Cell], int]:
        def outward_escape_cells(ref: str, pin_cell: _Cell) -> set[_Cell]:
            """Create a narrow (1-cell-wide) corridor from pin to outside
            component's blocked zone. Only the shortest outward direction."""
            escapes: set[_Cell] = set()
            comp = placement_map.get(ref)
            if comp is None:
                return escapes
            center = _to_cell(comp.x_mm, comp.y_mm, step_mm)
            dx = pin_cell.x - center.x
            dy = pin_cell.y - center.y

            # Determine escape direction(s) — prefer shortest path out
            candidates: list[tuple[int, int]] = []
            if abs(dx) >= abs(dy):
                sx = 1 if dx >= 0 else -1
                candidates = [(sx, 0), (0, 1 if dy >= 0 else -1)]
            else:
                sy = 1 if dy >= 0 else -1
                candidates = [(0, sy), (1 if dx >= 0 else -1, 0)]

            max_steps = 25
            for sdx, sdy in candidates:
                x, y = pin_cell.x, pin_cell.y
                corridor: list[_Cell] = []
                for _ in range(max_steps):
                    x += sdx
                    y += sdy
                    cell = _Cell(x, y)
                    corridor.append(cell)
                    if cell not in component_blocked:
                        escapes.update(corridor)
                        break
                else:
                    continue
                break

            return escapes

        net_pin_cells = {_to_cell(point[0], point[1], step_mm) for _, point in pin_points}
        # Map grid cells back to exact pin mm positions
        cell_to_exact: dict[_Cell, tuple[float, float]] = {}
        for _, point in pin_points:
            cell = _to_cell(point[0], point[1], step_mm)
            cell_to_exact[cell] = (point[0], point[1])

        net_pin_escapes: set[_Cell] = set()
        for node, point in pin_points:
            ref = node.split('.', 1)[0] if '.' in node else ''
            pin_cell = _to_cell(point[0], point[1], step_mm)
            net_pin_escapes |= outward_escape_cells(ref, pin_cell)
        tree_cells: set[_Cell] = set()
        segments: list[TraceSegment] = []

        _, root_xy = pin_points[0]
        root = _to_cell(root_xy[0], root_xy[1], step_mm)
        tree_cells.add(root)

        owners = dict(global_owner)
        penalties = defaultdict(float, global_penalty)
        connected_count = 1
        failed_goals: list[tuple[_Cell, tuple[float, float]]] = []

        unresolved = pin_points[1:]
        unresolved.sort(
            key=lambda item: abs(_to_cell(item[1][0], item[1][1], step_mm).x - root.x)
            + abs(_to_cell(item[1][0], item[1][1], step_mm).y - root.y)
        )

        for _, point in unresolved:
            goal = _to_cell(point[0], point[1], step_mm)
            if goal in tree_cells:
                connected_count += 1
                continue
            start = nearest_cell(goal, tree_cells)

            conflict_cells = {cell for cell, net_name in owners.items() if net_name != net.net_name}
            spacing_block = _inflate_cells(conflict_cells, radius=_USER_INFLATE_RADIUS)
            blocked = component_blocked | spacing_block

            for pin_cell in net_pin_cells:
                blocked.discard(pin_cell)
            for escape in net_pin_escapes:
                blocked.discard(escape)

            path = _astar_route(
                start=start,
                goal=goal,
                blocked=blocked,
                width_mm=width_mm,
                height_mm=height_mm,
                step_mm=step_mm,
                extra_cost=penalties,
                preferred_y=preferred_y,
                band_weight=0.02,
            )

            if path is None:
                path = choose_l_fallback(start, goal, component_blocked, conflict_cells)

            if path is None:
                failed_goals.append((goal, point))
                continue

            if len(path) < 2:
                if goal in tree_cells or start == goal:
                    connected_count += 1
                continue

            # Snap endpoints to exact pin positions
            start_exact = cell_to_exact.get(path[0])
            end_exact = (point[0], point[1])
            part_segments = _path_to_segments(path, step_mm,
                                              start_exact=start_exact,
                                              end_exact=end_exact)
            for seg in part_segments:
                seg.layer = "F.Cu"
            segments.extend(part_segments)

            for cell in path:
                owners[cell] = net.net_name
                penalties[cell] += 20.0
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    penalties[_Cell(cell.x + dx, cell.y + dy)] += 8.0
                for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    penalties[_Cell(cell.x + dx, cell.y + dy)] += 4.0
                tree_cells.add(cell)
            connected_count += 1

        if failed_goals:
            max_y = int(round(height_mm / step_mm))

            max_x = int(round(width_mm / step_mm))

            def h_corridor(start: _Cell, goal: _Cell, y_line: int) -> list[_Cell]:
                p1 = line_cells(start, _Cell(start.x, y_line))
                p2 = line_cells(_Cell(start.x, y_line), _Cell(goal.x, y_line))
                p3 = line_cells(_Cell(goal.x, y_line), goal)
                return p1 + p2[1:] + p3[1:]

            def v_corridor(start: _Cell, goal: _Cell, x_line: int) -> list[_Cell]:
                p1 = line_cells(start, _Cell(x_line, start.y))
                p2 = line_cells(_Cell(x_line, start.y), _Cell(x_line, goal.y))
                p3 = line_cells(_Cell(x_line, goal.y), goal)
                return p1 + p2[1:] + p3[1:]

            def perimeter_path(start: _Cell, goal: _Cell, edge: int) -> list[_Cell]:
                """Route via board edge. edge: 0=top, 1=bottom, 2=left, 3=right"""
                if edge == 0:
                    return (line_cells(start, _Cell(start.x, 1))
                            + line_cells(_Cell(start.x, 1), _Cell(goal.x, 1))[1:]
                            + line_cells(_Cell(goal.x, 1), goal)[1:])
                elif edge == 1:
                    return (line_cells(start, _Cell(start.x, max_y - 1))
                            + line_cells(_Cell(start.x, max_y - 1), _Cell(goal.x, max_y - 1))[1:]
                            + line_cells(_Cell(goal.x, max_y - 1), goal)[1:])
                elif edge == 2:
                    return (line_cells(start, _Cell(1, start.y))
                            + line_cells(_Cell(1, start.y), _Cell(1, goal.y))[1:]
                            + line_cells(_Cell(1, goal.y), goal)[1:])
                else:
                    return (line_cells(start, _Cell(max_x - 1, start.y))
                            + line_cells(_Cell(max_x - 1, start.y), _Cell(max_x - 1, goal.y))[1:]
                            + line_cells(_Cell(max_x - 1, goal.y), goal)[1:])

            def is_path_valid(path: list[_Cell], comp_block: set[_Cell],
                              conflict: set[_Cell], allowed: set[_Cell]) -> bool:
                for cell in path:
                    if cell in comp_block and cell not in allowed:
                        return False
                    if cell in conflict:
                        return False
                return True

            for goal, goal_exact in failed_goals:
                if goal in tree_cells:
                    connected_count += 1
                    continue
                start = nearest_cell(goal, tree_cells)
                conflict_cells = {cell for cell, net_name in owners.items() if net_name != net.net_name}
                allowed_cells = net_pin_cells | net_pin_escapes | tree_cells | {start, goal}

                chosen: list[_Cell] | None = None

                # Try horizontal corridors
                for y_line in range(1, max(2, max_y - 1)):
                    path = h_corridor(start, goal, y_line)
                    if is_path_valid(path, component_blocked, conflict_cells, allowed_cells):
                        chosen = path
                        break

                # Try vertical corridors
                if chosen is None:
                    for x_line in range(1, max(2, max_x - 1)):
                        path = v_corridor(start, goal, x_line)
                        if is_path_valid(path, component_blocked, conflict_cells, allowed_cells):
                            chosen = path
                            break

                # Try board-edge routing
                if chosen is None:
                    for edge in range(4):
                        path = perimeter_path(start, goal, edge)
                        if is_path_valid(path, component_blocked, conflict_cells, allowed_cells):
                            chosen = path
                            break

                if chosen is None:
                    continue

                start_exact = cell_to_exact.get(chosen[0])
                part_segments = _path_to_segments(chosen, step_mm,
                                                  start_exact=start_exact,
                                                  end_exact=goal_exact)
                for seg in part_segments:
                    seg.layer = "F.Cu"
                segments.extend(part_segments)
                for cell in chosen:
                    owners[cell] = net.net_name
                    penalties[cell] += 20.0
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        penalties[_Cell(cell.x + dx, cell.y + dy)] += 8.0
                    tree_cells.add(cell)
                connected_count += 1

        return segments, tree_cells, connected_count

    step_mm = 0.635  # half of 1.27mm KiCad grid, aligns well with pins
    placement_map = {item.ref: item for item in placements}
    comp_map = {comp.ref: comp for comp in plan.components}
    if pin_offset_override is not None:
        pin_offset_map = pin_offset_override
    else:
        pin_offset_map: dict[str, dict[str, tuple[float, float]]] = {}
        for ref, comp in comp_map.items():
            _, offsets = _footprint_geometry(comp.footprint)
            pin_offset_map[ref] = offsets

    component_blocked = _blocked_cells(placements, width_mm, height_mm, step_mm)
    component_penalty = _component_penalty_cells(placements, width_mm, height_mm, step_mm)
    owner_by_layer: dict[str, dict[_Cell, str]] = {"F.Cu": {}}
    route_penalty_by_layer: dict[str, dict[_Cell, float]] = {"F.Cu": defaultdict(float)}

    # Seed the penalty map with component-body avoidance costs
    for cell, cost in component_penalty.items():
        route_penalty_by_layer["F.Cu"][cell] += cost

    routed: list[RoutedNet] = []

    sorted_nets = sorted(plan.nets, key=lambda net: len(net.nodes), reverse=True)

    max_y_cell = int(round(height_mm / step_mm))
    for net_index, net in enumerate(sorted_nets):
        pin_points: list[tuple[str, tuple[float, float]]] = []
        for node in net.nodes:
            point = _pin_point(node, placement_map, pin_offset_map)
            if point is None:
                continue
            pin_points.append((node, point))

        if len(pin_points) < 2:
            routed.append(RoutedNet(net_name=net.net_name, nodes=net.nodes, segments=[]))
            continue

        cx = sum(p[1][0] for p in pin_points) / len(pin_points)
        cy = sum(p[1][1] for p in pin_points) / len(pin_points)
        pin_points.sort(key=lambda item: (item[1][0] - cx) ** 2 + (item[1][1] - cy) ** 2)
        preferred_y = int(((net_index + 0.5) / max(1, len(sorted_nets))) * max_y_cell)
        best_segments, best_cells, _ = route_net_on_layer(
            net=net,
            pin_points=pin_points,
            global_owner=owner_by_layer["F.Cu"],
            global_penalty=route_penalty_by_layer["F.Cu"],
            component_blocked=component_blocked,
            preferred_y=preferred_y,
        )

        for cell in best_cells:
            owner_by_layer["F.Cu"][cell] = net.net_name
            route_penalty_by_layer["F.Cu"][cell] += 8.0

        seen_in_net: set[tuple[float, float, float, float, str]] = set()
        filtered_segments: list[TraceSegment] = []
        for seg in best_segments:
            a = (seg.x1_mm, seg.y1_mm)
            b = (seg.x2_mm, seg.y2_mm)
            p1, p2 = (a, b) if a <= b else (b, a)
            key = (p1[0], p1[1], p2[0], p2[1], seg.layer)
            if key in seen_in_net:
                continue
            seen_in_net.add(key)
            filtered_segments.append(seg)

        routed.append(RoutedNet(net_name=net.net_name, nodes=net.nodes, segments=filtered_segments))

    # ── Rip-up and reroute: retry unrouted nets ──────────────────────
    for _rip_round in range(3):
        unrouted_indices = [
            i for i, rn in enumerate(routed)
            if len(rn.segments) == 0
            and sum(1 for n in plan.nets if n.net_name == rn.net_name and len(n.nodes) >= 2)
        ]
        if not unrouted_indices:
            break

        for ui in unrouted_indices:
            fail_net = routed[ui]
            fail_plan_net = next(
                (n for n in plan.nets if n.net_name == fail_net.net_name), None
            )
            if fail_plan_net is None:
                continue

            # Collect pin points for the failed net
            fail_pins: list[tuple[str, tuple[float, float]]] = []
            for node in fail_plan_net.nodes:
                point = _pin_point(node, placement_map, pin_offset_map)
                if point is not None:
                    fail_pins.append((node, point))
            if len(fail_pins) < 2:
                continue

            fail_pin_cells = {_to_cell(p[0], p[1], step_mm) for _, p in fail_pins}

            # Find which routed net is blocking the path between pins
            start_cell = _to_cell(fail_pins[0][1][0], fail_pins[0][1][1], step_mm)
            goal_cell = _to_cell(fail_pins[-1][1][0], fail_pins[-1][1][1], step_mm)

            blocking_net_names: set[str] = set()
            # Check cells in the rectangle between start and goal
            rx1 = min(start_cell.x, goal_cell.x) - 4
            rx2 = max(start_cell.x, goal_cell.x) + 4
            ry1 = min(start_cell.y, goal_cell.y) - 4
            ry2 = max(start_cell.y, goal_cell.y) + 4
            for cell, owner_net in owner_by_layer["F.Cu"].items():
                if owner_net != fail_net.net_name and rx1 <= cell.x <= rx2 and ry1 <= cell.y <= ry2:
                    blocking_net_names.add(owner_net)

            if not blocking_net_names:
                continue

            # Pick the blocking net with fewest segments (least impactful to remove)
            victim_name = min(
                blocking_net_names,
                key=lambda nn: sum(len(r.segments) for r in routed if r.net_name == nn),
            )

            # Remove victim net from owner map
            victim_cells = {
                cell for cell, nn in list(owner_by_layer["F.Cu"].items()) if nn == victim_name
            }
            for cell in victim_cells:
                del owner_by_layer["F.Cu"][cell]
                route_penalty_by_layer["F.Cu"][cell] = max(
                    0.0, route_penalty_by_layer["F.Cu"].get(cell, 0.0) - 8.0
                )

            # Re-route the failed net first (it gets priority now)
            cx = sum(p[1][0] for p in fail_pins) / len(fail_pins)
            cy = sum(p[1][1] for p in fail_pins) / len(fail_pins)
            fail_pins.sort(key=lambda it: (it[1][0] - cx) ** 2 + (it[1][1] - cy) ** 2)
            pref_y = max_y_cell // 2
            new_segs, new_cells, _ = route_net_on_layer(
                net=fail_plan_net,
                pin_points=fail_pins,
                global_owner=owner_by_layer["F.Cu"],
                global_penalty=route_penalty_by_layer["F.Cu"],
                component_blocked=component_blocked,
                preferred_y=pref_y,
            )
            for cell in new_cells:
                owner_by_layer["F.Cu"][cell] = fail_plan_net.net_name
                route_penalty_by_layer["F.Cu"][cell] += 8.0
            routed[ui] = RoutedNet(
                net_name=fail_net.net_name, nodes=fail_net.nodes, segments=new_segs
            )

            # Re-route the victim net
            victim_plan_net = next(
                (n for n in plan.nets if n.net_name == victim_name), None
            )
            victim_idx = next(
                (idx for idx, r in enumerate(routed) if r.net_name == victim_name), None
            )
            if victim_plan_net is not None and victim_idx is not None:
                v_pins: list[tuple[str, tuple[float, float]]] = []
                for node in victim_plan_net.nodes:
                    pt = _pin_point(node, placement_map, pin_offset_map)
                    if pt is not None:
                        v_pins.append((node, pt))
                if len(v_pins) >= 2:
                    vcx = sum(p[1][0] for p in v_pins) / len(v_pins)
                    vcy = sum(p[1][1] for p in v_pins) / len(v_pins)
                    v_pins.sort(key=lambda it: (it[1][0] - vcx) ** 2 + (it[1][1] - vcy) ** 2)
                    vs, vc, _ = route_net_on_layer(
                        net=victim_plan_net,
                        pin_points=v_pins,
                        global_owner=owner_by_layer["F.Cu"],
                        global_penalty=route_penalty_by_layer["F.Cu"],
                        component_blocked=component_blocked,
                        preferred_y=max_y_cell // 3,
                    )
                    for cell in vc:
                        owner_by_layer["F.Cu"][cell] = victim_name
                        route_penalty_by_layer["F.Cu"][cell] += 8.0
                    routed[victim_idx] = RoutedNet(
                        net_name=victim_name,
                        nodes=victim_plan_net.nodes,
                        segments=vs,
                    )

    # ── Post-process: remove cross-net overlapping segments ──────────
    global_seg_owner: dict[tuple[float, float, float, float, str], str] = {}
    for rn in routed:
        clean_segs: list[TraceSegment] = []
        for seg in rn.segments:
            a = (seg.x1_mm, seg.y1_mm)
            b = (seg.x2_mm, seg.y2_mm)
            p1, p2 = (a, b) if a <= b else (b, a)
            key = (p1[0], p1[1], p2[0], p2[1], seg.layer)
            existing = global_seg_owner.get(key)
            if existing is None or existing == rn.net_name:
                global_seg_owner[key] = rn.net_name
                clean_segs.append(seg)
            # else: drop this segment (belongs to another net)
        rn.segments = clean_segs

    # ── Post-process: add stub traces for unreached pins ─────────────
    for rn in routed:
        if not rn.segments:
            continue
        plan_net = next((n for n in plan.nets if n.net_name == rn.net_name), None)
        if plan_net is None:
            continue

        # Collect all trace endpoints in this net
        trace_points: list[tuple[float, float]] = []
        for seg in rn.segments:
            trace_points.append((seg.x1_mm, seg.y1_mm))
            trace_points.append((seg.x2_mm, seg.y2_mm))
        if not trace_points:
            continue

        # Check each pin in the net
        for node in plan_net.nodes:
            pin_pt = _pin_point(node, placement_map, pin_offset_map)
            if pin_pt is None:
                continue
            px, py = pin_pt

            # Check if any trace endpoint is within 1.5mm of this pin
            min_dist = min(math.dist((px, py), tp) for tp in trace_points)
            if min_dist <= 1.5:
                continue

            # This pin is unreached — add an L-shaped stub from pin to nearest trace point
            nearest = min(trace_points, key=lambda tp: math.dist((px, py), tp))
            nx, ny = nearest

            # Create L-shaped connection (horizontal then vertical)
            elbow_x, elbow_y = nx, py  # go horizontal to align x, then vertical
            seg1 = TraceSegment(
                x1_mm=round(px, 3), y1_mm=round(py, 3),
                x2_mm=round(elbow_x, 3), y2_mm=round(elbow_y, 3),
                layer="F.Cu",
            )
            seg2 = TraceSegment(
                x1_mm=round(elbow_x, 3), y1_mm=round(elbow_y, 3),
                x2_mm=round(nx, 3), y2_mm=round(ny, 3),
                layer="F.Cu",
            )
            # Only add non-zero-length segments
            if not (seg1.x1_mm == seg1.x2_mm and seg1.y1_mm == seg1.y2_mm):
                rn.segments.append(seg1)
            if not (seg2.x1_mm == seg2.x2_mm and seg2.y1_mm == seg2.y2_mm):
                rn.segments.append(seg2)

            # Update trace points for subsequent pins
            trace_points.append((px, py))
            trace_points.append((elbow_x, elbow_y))

    return routed


def compute_initial_placement(
    plan: DesignPlan, width_mm: float, height_mm: float,
) -> list[ComponentPlacement]:
    """Run the force-directed auto-placement and return placements.

    This is the public wrapper around ``_layout_components`` so that
    external modules (e.g. the placement canvas) can obtain a good
    starting layout without accessing private helpers.
    """
    return _layout_components(plan, width_mm, height_mm)


def route_with_fixed_placements(
    plan: DesignPlan,
    placements: list[ComponentPlacement],
    width_mm: float,
    height_mm: float,
) -> BoardLayout:
    """Route nets using pre-determined component positions.

    Unlike ``generate_board_layout`` this skips the auto-placement step
    and uses the provided *placements* directly (e.g. from user
    drag-and-drop on the canvas).  Rotation is taken into account when
    computing pin positions.
    """
    comp_map = {c.ref: c for c in plan.components}

    # Build pin offsets with rotation applied
    pin_offset_map: dict[str, dict[str, tuple[float, float]]] = {}
    for p in placements:
        comp = comp_map.get(p.ref)
        if not comp:
            continue
        _, raw_pins = _footprint_geometry(comp.footprint)
        rot = p.rotation_deg
        if rot != 0.0:
            rad = math.radians(rot)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            rotated: dict[str, tuple[float, float]] = {}
            for pin_name, (dx, dy) in raw_pins.items():
                rotated[pin_name] = (
                    round(dx * cos_r - dy * sin_r, 3),
                    round(dx * sin_r + dy * cos_r, 3),
                )
            pin_offset_map[p.ref] = rotated
        else:
            pin_offset_map[p.ref] = raw_pins

    routed_nets = _route_nets(
        plan, placements, width_mm, height_mm,
        pin_offset_override=pin_offset_map,
    )

    total_segments = sum(len(n.segments) for n in routed_nets)
    total_length = 0.0
    for net in routed_nets:
        for seg in net.segments:
            total_length += math.dist(
                (seg.x1_mm, seg.y1_mm), (seg.x2_mm, seg.y2_mm))

    return BoardLayout(
        width_mm=width_mm,
        height_mm=height_mm,
        placements=placements,
        routed_nets=routed_nets,
        metrics={
            "component_count": float(len(placements)),
            "net_count": float(len(routed_nets)),
            "segment_count": float(total_segments),
            "total_trace_length_mm": round(total_length, 3),
            "board_area_mm2": round(width_mm * height_mm, 3),
        },
    )


def generate_board_layout(plan: DesignPlan, spec: ProjectSpec) -> BoardLayout:
    base_w, base_h = _parse_outline_mm(spec.board_outline)
    total_nets_with_pins = sum(1 for n in plan.nets if len(n.nodes) >= 2)

    # Ensure board is large enough for components + routing channels
    total_comp_area = 0.0
    for comp in plan.components:
        cw, ch = _component_size(comp.ref, comp.footprint)
        total_comp_area += (cw + _USER_COMP_SPACING) * (ch + _USER_COMP_SPACING)
    min_area = total_comp_area * _USER_BOARD_AREA_MULT
    if base_w * base_h < min_area:
        scale_up = math.sqrt(min_area / (base_w * base_h))
        base_w = round(base_w * scale_up, 1)
        base_h = round(base_h * scale_up, 1)

    best_placements: list[ComponentPlacement] | None = None
    best_routed: list[RoutedNet] | None = None
    best_connected = -1
    best_length = float("inf")
    best_w = base_w
    best_h = base_h

    # Try progressively larger boards if routing fails
    for scale in (1.0, 1.3, 1.6):
        width_mm = round(base_w * scale, 1)
        height_mm = round(base_h * scale, 1)

        for seed in list(range(10, 140, 6)):
            placements = _layout_components(plan, width_mm, height_mm, seed=seed)
            routed_nets = _route_nets(plan, placements, width_mm, height_mm)

            seg_map = {net.net_name: len(net.segments) for net in routed_nets}
            connected = 0
            for net in plan.nets:
                if len(net.nodes) < 2 or seg_map.get(net.net_name, 0) > 0:
                    connected += 1

            total_length = 0.0
            for net in routed_nets:
                for seg in net.segments:
                    total_length += math.dist((seg.x1_mm, seg.y1_mm), (seg.x2_mm, seg.y2_mm))

            if connected > best_connected or (connected == best_connected and total_length < best_length):
                best_connected = connected
                best_length = total_length
                best_placements = placements
                best_routed = routed_nets
                best_w = width_mm
                best_h = height_mm

            # Early exit if all nets routed
            if best_connected >= total_nets_with_pins:
                break

        if best_connected >= total_nets_with_pins:
            break

    width_mm = best_w
    height_mm = best_h
    placements = best_placements or _layout_components(plan, width_mm, height_mm)
    routed_nets = best_routed or _route_nets(plan, placements, width_mm, height_mm)

    total_segments = sum(len(net.segments) for net in routed_nets)
    total_length = 0.0
    for net in routed_nets:
        for seg in net.segments:
            total_length += math.dist((seg.x1_mm, seg.y1_mm), (seg.x2_mm, seg.y2_mm))

    metrics = {
        "component_count": float(len(placements)),
        "net_count": float(len(routed_nets)),
        "segment_count": float(total_segments),
        "total_trace_length_mm": round(total_length, 3),
        "board_area_mm2": round(width_mm * height_mm, 3),
    }

    return BoardLayout(
        width_mm=width_mm,
        height_mm=height_mm,
        placements=placements,
        routed_nets=routed_nets,
        metrics=metrics,
    )
