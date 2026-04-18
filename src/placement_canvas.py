"""Interactive PCB component placement canvas.

Drag-and-drop components on a board outline, with real-time ratsnest
(connection) lines.  After the user finalises placement, the caller
retrieves positions via ``get_placements()`` and feeds them to the
routing engine.

Controls
--------
- **Left-click + drag** — move a component
- **Right-click + drag** — pan the view
- **Scroll wheel** — zoom in / out
- **R key** — rotate selected component 90°
"""
from __future__ import annotations

import math
import tkinter as tk
from typing import Callable

from .models import ComponentPlacement, DesignPlan, RoutedNet

# ── Colour palette (Catppuccin Mocha) ──────────────────────────
_BG        = "#1e1e2e"
_BOARD_BG  = "#181825"
_BOARD_OUT = "#585b70"
_GRID      = "#242438"
_FG        = "#cdd6f4"
_DIM       = "#6c7086"
_ACCENT    = "#89b4fa"
_PIN_CLR   = "#f5e0dc"
_PIN1_CLR  = "#f38ba8"

_COMP_COLORS: dict[str, str] = {
    "U":  "#89b4fa",   # IC – blue
    "R":  "#fab387",   # Resistor – peach
    "C":  "#a6e3a1",   # Capacitor – green
    "D":  "#f38ba8",   # Diode / LED – pink
    "Q":  "#cba6f7",   # Transistor – mauve
    "J":  "#9399b2",   # Connector – gray
    "L":  "#f9e2af",   # Inductor – yellow
    "SW": "#94e2d5",   # Switch – teal
    "F":  "#eba0ac",   # Fuse – maroon
}

_NET_COLORS = [
    "#f38ba8", "#a6e3a1", "#89b4fa", "#fab387", "#cba6f7",
    "#f9e2af", "#94e2d5", "#74c7ec", "#eba0ac", "#b4befe",
]

_SNAP_MM = 1.27  # half of standard 2.54 mm pitch


# ════════════════════════════════════════════════════════════════
class PlacementCanvas(tk.Frame):
    """Drag-and-drop component placement on a PCB board outline."""

    def __init__(
        self,
        parent: tk.Widget,
        on_status: Callable[[str], None] | None = None,
        **kw,
    ):
        super().__init__(parent, bg=_BG, **kw)
        self._on_status = on_status or (lambda _: None)

        # ── view ──
        self._scale: float = 10.0
        self._pan_x: float = 50.0
        self._pan_y: float = 50.0
        self._min_scale, self._max_scale = 3.0, 30.0

        # ── design data ──
        self._plan: DesignPlan | None = None
        self._board_w: float = 50.0
        self._board_h: float = 40.0
        self._positions: dict[str, tuple[float, float]] = {}
        self._rotations: dict[str, float] = {}
        self._sizes: dict[str, tuple[float, float]] = {}
        self._pin_offsets: dict[str, dict[str, tuple[float, float]]] = {}
        self._comp_map: dict[str, object] = {}
        self._routed_nets: list[RoutedNet] = []

        # ── interaction ──
        self._selected: str | None = None
        self._drag_px: tuple[float, float] | None = None
        self._drag_mm: tuple[float, float] | None = None
        self._pan_anchor: tuple[float, float] | None = None

        self._build_widgets()

    # ────────────────────────────────────────────────────────────
    # Widget construction
    # ────────────────────────────────────────────────────────────
    def _build_widgets(self):
        # toolbar
        tb = tk.Frame(self, bg="#181825")
        tb.pack(fill="x", side="top")
        bkw: dict = dict(
            bg="#313244", fg=_FG, relief="flat", bd=0,
            padx=8, pady=4, font=("Segoe UI", 9),
        )
        tk.Button(tb, text="🔍+", command=self._zoom_in, **bkw).pack(
            side="left", padx=2, pady=3)
        tk.Button(tb, text="🔍−", command=self._zoom_out, **bkw).pack(
            side="left", padx=2, pady=3)
        tk.Button(tb, text="⟲ Sığdır", command=self._fit_view, **bkw).pack(
            side="left", padx=2, pady=3)

        tk.Frame(tb, width=2, bg="#45475a").pack(
            side="left", fill="y", padx=6, pady=4)

        # ── Rotation buttons ──
        tk.Button(tb, text="↻ 90°", command=self._rotate_cw, **bkw).pack(
            side="left", padx=2, pady=3)
        tk.Button(tb, text="↺ 90°", command=self._rotate_ccw, **bkw).pack(
            side="left", padx=2, pady=3)
        tk.Button(tb, text="⇅ 180°", command=self._rotate_180, **bkw).pack(
            side="left", padx=2, pady=3)
        tk.Button(tb, text="↻ 0° Sıfırla", command=self._rotate_reset, **bkw).pack(
            side="left", padx=2, pady=3)
        self._rot_lbl = tk.Label(
            tb, text="", bg="#181825", fg="#f9e2af", font=("Consolas", 9, "bold"))
        self._rot_lbl.pack(side="left", padx=(6, 2))

        tk.Frame(tb, width=2, bg="#45475a").pack(
            side="left", fill="y", padx=6, pady=4)

        self._snap_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tb, text="Snap", variable=self._snap_var,
            bg="#181825", fg=_FG, selectcolor="#313244",
            activebackground="#181825", font=("Segoe UI", 9),
        ).pack(side="left", padx=4)

        self._nets_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tb, text="Bağlantılar", variable=self._nets_var,
            bg="#181825", fg=_FG, selectcolor="#313244",
            activebackground="#181825", font=("Segoe UI", 9),
            command=self._full_redraw,
        ).pack(side="left", padx=4)

        self._routes_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tb, text="Yollar", variable=self._routes_var,
            bg="#181825", fg=_FG, selectcolor="#313244",
            activebackground="#181825", font=("Segoe UI", 9),
            command=self._full_redraw,
        ).pack(side="left", padx=4)

        self._coord_lbl = tk.Label(
            tb, text="", bg="#181825", fg=_DIM, font=("Consolas", 9))
        self._coord_lbl.pack(side="right", padx=8)

        self._info_lbl = tk.Label(
            tb, bg="#181825", fg=_ACCENT, font=("Segoe UI", 9),
            text="Sürükle: Yerleştir  |  R/E: Döndür ↻↺  |  0: Sıfırla  |  Scroll: Zoom  |  Sağ-tık: Kaydır",
        )
        self._info_lbl.pack(side="right", padx=8)

        # canvas
        self._cv = tk.Canvas(self, bg=_BG, highlightthickness=0, cursor="crosshair")
        self._cv.pack(fill="both", expand=True)

        # bindings
        self._cv.bind("<Button-1>", self._press)
        self._cv.bind("<B1-Motion>", self._drag)
        self._cv.bind("<ButtonRelease-1>", self._release)
        self._cv.bind("<Button-3>", self._pan_start)
        self._cv.bind("<B3-Motion>", self._pan_move)
        self._cv.bind("<ButtonRelease-3>", self._pan_end)
        self._cv.bind("<Motion>", self._motion)
        self._cv.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        self._cv.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.15))
        self._cv.bind("<MouseWheel>", self._wheel)
        self._cv.bind("<KeyPress-r>", self._rotate_sel)
        self._cv.bind("<KeyPress-R>", self._rotate_sel)
        self._cv.bind("<KeyPress-e>", lambda e: self._rotate_ccw())
        self._cv.bind("<KeyPress-E>", lambda e: self._rotate_ccw())
        self._cv.bind("<KeyPress-0>", lambda e: self._rotate_reset())
        self._cv.focus_set()

        # placeholder
        self._cv.create_text(
            300, 200,
            text='Henüz tasarım yok.\n"▶  Üret" butonuna basarak başlayın.',
            fill=_DIM, font=("Segoe UI", 14), tags="placeholder",
        )

    # ────────────────────────────────────────────────────────────
    # Coordinate helpers
    # ────────────────────────────────────────────────────────────
    def _mm2px(self, x: float, y: float) -> tuple[float, float]:
        return self._pan_x + x * self._scale, self._pan_y + y * self._scale

    def _px2mm(self, px: float, py: float) -> tuple[float, float]:
        return (px - self._pan_x) / self._scale, (py - self._pan_y) / self._scale

    def _snap(self, x: float, y: float) -> tuple[float, float]:
        if self._snap_var.get():
            return round(x / _SNAP_MM) * _SNAP_MM, round(y / _SNAP_MM) * _SNAP_MM
        return x, y

    # ────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────
    def load_plan(
        self,
        plan: DesignPlan,
        board_w: float,
        board_h: float,
        initial_positions: dict[str, tuple[float, float]] | None = None,
    ):
        """Load a design plan and populate the canvas."""
        from .layout_router import _footprint_geometry

        self._plan = plan
        self._board_w = board_w
        self._board_h = board_h
        self._comp_map = {c.ref: c for c in plan.components}
        self._selected = None
        self._routed_nets = []

        for c in plan.components:
            sz, pins = _footprint_geometry(c.footprint)
            self._sizes[c.ref] = sz
            self._pin_offsets[c.ref] = pins
            self._rotations[c.ref] = 0.0

        if initial_positions:
            self._positions = dict(initial_positions)
        else:
            self._simple_grid_place()

        self._fit_view()

    def get_placements(self) -> list[ComponentPlacement]:
        """Return current positions as ComponentPlacement list."""
        if not self._plan:
            return []
        out: list[ComponentPlacement] = []
        for c in self._plan.components:
            ref = c.ref
            x, y = self._positions.get(ref, (10.0, 10.0))
            ow, oh = self._sizes.get(ref, (6.0, 4.0))
            rot = self._rotations.get(ref, 0.0)
            w, h = (oh, ow) if rot in (90.0, 270.0) else (ow, oh)
            out.append(ComponentPlacement(
                ref=ref,
                x_mm=round(x, 3),
                y_mm=round(y, 3),
                rotation_deg=rot,
                width_mm=round(w, 3),
                height_mm=round(h, 3),
            ))
        return out

    def show_routes(self, routed_nets: list[RoutedNet]):
        """Show routed traces on the canvas."""
        self._routed_nets = list(routed_nets)
        self._full_redraw()

    def clear_routes(self):
        self._routed_nets = []
        self._cv.delete("route")

    def auto_place(self):
        """Re-run the force-directed auto-placement algorithm."""
        if not self._plan:
            return
        from .layout_router import _layout_components

        placements = _layout_components(
            self._plan, self._board_w, self._board_h)
        for p in placements:
            self._positions[p.ref] = (p.x_mm, p.y_mm)
            self._rotations[p.ref] = 0.0
        self._routed_nets = []
        self._full_redraw()

    # ────────────────────────────────────────────────────────────
    # Drawing
    # ────────────────────────────────────────────────────────────
    def _full_redraw(self):
        self._cv.delete("all")
        if not self._plan:
            cw = self._cv.winfo_width() or 600
            ch = self._cv.winfo_height() or 400
            self._cv.create_text(
                cw / 2, ch / 2,
                text='Henüz tasarım yok.\n"▶  Üret" butonuna basarak başlayın.',
                fill=_DIM, font=("Segoe UI", 14),
            )
            return
        self._draw_board()
        self._draw_grid()
        # Draw routes first (behind components)
        if self._routes_var.get() and self._routed_nets:
            self._draw_routes()
        for c in self._plan.components:
            self._draw_comp(c.ref)
        if self._nets_var.get():
            self._draw_ratsnest()

    def _draw_board(self):
        x1, y1 = self._mm2px(0, 0)
        x2, y2 = self._mm2px(self._board_w, self._board_h)
        self._cv.create_rectangle(
            x1, y1, x2, y2,
            outline=_BOARD_OUT, fill=_BOARD_BG, width=2, tags="board")
        # dimension labels
        self._cv.create_text(
            (x1 + x2) / 2, y1 - 10,
            text=f"{self._board_w:.1f} mm", fill=_DIM,
            font=("Consolas", 8), tags="board")
        self._cv.create_text(
            x1 - 10, (y1 + y2) / 2,
            text=f"{self._board_h:.1f} mm", fill=_DIM,
            font=("Consolas", 8), angle=90, tags="board")

    def _draw_grid(self):
        if self._scale < 4:
            return
        step = 2.54
        bx0, by0 = self._mm2px(0, 0)
        bx1, by1 = self._mm2px(self._board_w, self._board_h)
        mm = 0.0
        while mm <= self._board_w:
            px, _ = self._mm2px(mm, 0)
            self._cv.create_line(px, by0, px, by1, fill=_GRID, tags="grid")
            mm += step
        mm = 0.0
        while mm <= self._board_h:
            _, py = self._mm2px(0, mm)
            self._cv.create_line(bx0, py, bx1, py, fill=_GRID, tags="grid")
            mm += step

    # ── component colours ──────────────────────────────────────
    @staticmethod
    def _color_for(ref: str) -> str:
        for pfx, clr in _COMP_COLORS.items():
            if ref.startswith(pfx):
                return clr
        return _FG

    @staticmethod
    def _darken(hx: str, f: float = 0.25) -> str:
        r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
        return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

    # ── rotated pin offset ─────────────────────────────────────
    def _rot_pin(self, ref: str, pin: str) -> tuple[float, float]:
        dx, dy = self._pin_offsets.get(ref, {}).get(pin, (0.0, 0.0))
        rot = self._rotations.get(ref, 0.0)
        if rot == 90:
            dx, dy = -dy, dx
        elif rot == 180:
            dx, dy = -dx, -dy
        elif rot == 270:
            dx, dy = dy, -dx
        return dx, dy

    # ── draw one component ─────────────────────────────────────
    def _draw_comp(self, ref: str):
        tag = f"c_{ref}"
        self._cv.delete(tag)
        cx, cy = self._positions.get(ref, (10, 10))
        ow, oh = self._sizes.get(ref, (6, 4))
        rot = self._rotations.get(ref, 0.0)
        w, h = (oh, ow) if rot in (90, 270) else (ow, oh)

        color = self._color_for(ref)
        sel = ref == self._selected
        out_c = "#f9e2af" if sel else color
        out_w = 3 if sel else 2

        x1, y1 = self._mm2px(cx - w / 2, cy - h / 2)
        x2, y2 = self._mm2px(cx + w / 2, cy + h / 2)
        self._cv.create_rectangle(
            x1, y1, x2, y2,
            outline=out_c, fill=self._darken(color), width=out_w,
            tags=(tag, "comp"))

        # pin-1 notch (small triangle at top-left to indicate orientation)
        if rot in (0, 90, 180, 270):
            notch_sz = max(3, self._scale * 0.5)
            # notch goes at the pin-1 corner
            if rot == 0:
                nx1, ny1 = x1, y1
                ntri = [(nx1, ny1), (nx1 + notch_sz, ny1), (nx1, ny1 + notch_sz)]
            elif rot == 90:
                nx1, ny1 = x2, y1
                ntri = [(nx1, ny1), (nx1, ny1 + notch_sz), (nx1 - notch_sz, ny1)]
            elif rot == 180:
                nx1, ny1 = x2, y2
                ntri = [(nx1, ny1), (nx1 - notch_sz, ny1), (nx1, ny1 - notch_sz)]
            else:  # 270
                nx1, ny1 = x1, y2
                ntri = [(nx1, ny1), (nx1, ny1 - notch_sz), (nx1 + notch_sz, ny1)]
            self._cv.create_polygon(
                *[c for pt in ntri for c in pt],
                fill=_PIN1_CLR, outline="", tags=(tag, "comp"))

        # rotation angle badge (shown when rotated)
        if rot != 0:
            badge_x = x2 - 2
            badge_y = y1 + 2
            self._cv.create_text(
                badge_x, badge_y, text=f"{int(rot)}°",
                fill="#f9e2af", font=("Consolas", 7, "bold"),
                anchor="ne", tags=(tag, "comp"))

        # label
        px_c, py_c = self._mm2px(cx, cy)
        comp = self._comp_map.get(ref)
        lbl = f"{ref}\n{comp.value}" if comp and comp.value else ref
        fs = max(7, min(11, int(self._scale * 0.9)))
        self._cv.create_text(
            px_c, py_c, text=lbl,
            fill=_FG, font=("Segoe UI", fs, "bold"), tags=(tag, "comp"))

        # pins
        offsets = self._pin_offsets.get(ref, {})
        for pname in offsets:
            dx, dy = self._rot_pin(ref, pname)
            ppx, ppy = self._mm2px(cx + dx, cy + dy)
            r = max(2, self._scale * 0.3)
            fill = _PIN1_CLR if pname == "1" else _PIN_CLR
            self._cv.create_oval(
                ppx - r, ppy - r, ppx + r, ppy + r,
                fill=fill, outline="", tags=(tag, "comp"))
            if self._scale > 8:
                self._cv.create_text(
                    ppx, ppy - r - 4, text=pname,
                    fill=_DIM, font=("Consolas", 7), tags=(tag, "comp"))

    # ── ratsnest (dashed connection lines) ─────────────────────
    def _draw_ratsnest(self):
        if not self._plan:
            return
        for i, net in enumerate(self._plan.nets):
            clr = _NET_COLORS[i % len(_NET_COLORS)]
            pts: list[tuple[float, float]] = []
            for node in net.nodes:
                if "." not in node:
                    continue
                ref, pin = node.split(".", 1)
                if ref not in self._positions:
                    continue
                cx, cy = self._positions[ref]
                dx, dy = self._rot_pin(ref, pin)
                pts.append((cx + dx, cy + dy))
            if len(pts) < 2:
                continue
            ax, ay = pts[0]
            for bx, by in pts[1:]:
                px1, py1 = self._mm2px(ax, ay)
                px2, py2 = self._mm2px(bx, by)
                self._cv.create_line(
                    px1, py1, px2, py2,
                    fill=clr, width=1, dash=(4, 3), tags="ratsnest")

    # ── routed traces (solid) ──────────────────────────────────
    def _draw_routes(self):
        for i, rn in enumerate(self._routed_nets):
            clr = _NET_COLORS[i % len(_NET_COLORS)]
            for seg in rn.segments:
                px1, py1 = self._mm2px(seg.x1_mm, seg.y1_mm)
                px2, py2 = self._mm2px(seg.x2_mm, seg.y2_mm)
                w = max(1.5, self._scale * 0.25)
                self._cv.create_line(
                    px1, py1, px2, py2,
                    fill=clr, width=w, tags="route")

    # ────────────────────────────────────────────────────────────
    # Interaction handlers
    # ────────────────────────────────────────────────────────────
    def _hit_comp(self, px: float, py: float) -> str | None:
        items = self._cv.find_overlapping(px - 3, py - 3, px + 3, py + 3)
        for item in items:
            for t in self._cv.gettags(item):
                if t.startswith("c_"):
                    return t[2:]
        return None

    def _press(self, event):
        self._cv.focus_set()
        ref = self._hit_comp(event.x, event.y)
        old = self._selected
        self._selected = ref
        if old and old != ref:
            self._draw_comp(old)
        if ref:
            self._draw_comp(ref)
            self._drag_px = (event.x, event.y)
            self._drag_mm = self._positions.get(ref)
            self._update_sel_info(ref)
        else:
            self._drag_px = None
            self._info_lbl.config(
                text="Sürükle: Yerleştir  |  R/E: Döndür ↻↺  |  0: Sıfırla  |  Scroll: Zoom")
            self._rot_lbl.config(text="")

    def _drag(self, event):
        if not self._selected or not self._drag_px or not self._drag_mm:
            return
        dx = (event.x - self._drag_px[0]) / self._scale
        dy = (event.y - self._drag_px[1]) / self._scale
        nx, ny = self._snap(self._drag_mm[0] + dx, self._drag_mm[1] + dy)
        # clamp inside board
        ow, oh = self._sizes.get(self._selected, (6, 4))
        rot = self._rotations.get(self._selected, 0.0)
        w, h = (oh, ow) if rot in (90, 270) else (ow, oh)
        nx = max(w / 2, min(self._board_w - w / 2, nx))
        ny = max(h / 2, min(self._board_h - h / 2, ny))
        self._positions[self._selected] = (nx, ny)

        self._draw_comp(self._selected)
        self._cv.delete("ratsnest")
        if self._nets_var.get():
            self._draw_ratsnest()
        self._coord_lbl.config(text=f"({nx:.2f}, {ny:.2f}) mm")

    def _release(self, event):
        if self._drag_px is not None:
            self._drag_px = None
            self._drag_mm = None
            self._full_redraw()

    def _pan_start(self, event):
        self._pan_anchor = (event.x, event.y)

    def _pan_move(self, event):
        if not self._pan_anchor:
            return
        self._pan_x += event.x - self._pan_anchor[0]
        self._pan_y += event.y - self._pan_anchor[1]
        self._pan_anchor = (event.x, event.y)
        self._full_redraw()

    def _pan_end(self, event):
        self._pan_anchor = None

    def _motion(self, event):
        mx, my = self._px2mm(event.x, event.y)
        self._coord_lbl.config(text=f"({mx:.2f}, {my:.2f}) mm")

    def _wheel(self, event):
        f = 1.15 if event.delta > 0 else 1 / 1.15
        self._zoom_at(event.x, event.y, f)

    # ── zoom ───────────────────────────────────────────────────
    def _zoom_at(self, px: float, py: float, factor: float):
        ns = max(self._min_scale, min(self._max_scale, self._scale * factor))
        if ns == self._scale:
            return
        mx, my = self._px2mm(px, py)
        self._scale = ns
        self._pan_x = px - mx * ns
        self._pan_y = py - my * ns
        self._full_redraw()

    def _zoom_in(self):
        w = self._cv.winfo_width() / 2
        h = self._cv.winfo_height() / 2
        self._zoom_at(w, h, 1.3)

    def _zoom_out(self):
        w = self._cv.winfo_width() / 2
        h = self._cv.winfo_height() / 2
        self._zoom_at(w, h, 1 / 1.3)

    def _fit_view(self):
        self._cv.update_idletasks()
        cw = max(400, self._cv.winfo_width())
        ch = max(300, self._cv.winfo_height())
        margin = 60
        sx = (cw - 2 * margin) / max(1, self._board_w)
        sy = (ch - 2 * margin) / max(1, self._board_h)
        self._scale = max(self._min_scale, min(self._max_scale, min(sx, sy)))
        bw = self._board_w * self._scale
        bh = self._board_h * self._scale
        self._pan_x = (cw - bw) / 2
        self._pan_y = (ch - bh) / 2
        self._full_redraw()

    def _rotate_sel(self, event=None):
        """R key — rotate selected 90° clockwise."""
        self._rotate_cw()

    def _rotate_cw(self):
        """Rotate selected component 90° clockwise."""
        if not self._selected:
            return
        ref = self._selected
        self._rotations[ref] = (self._rotations.get(ref, 0.0) + 90) % 360
        self._after_rotate(ref)

    def _rotate_ccw(self):
        """Rotate selected component 90° counter-clockwise."""
        if not self._selected:
            return
        ref = self._selected
        self._rotations[ref] = (self._rotations.get(ref, 0.0) - 90) % 360
        self._after_rotate(ref)

    def _rotate_180(self):
        """Rotate selected component 180°."""
        if not self._selected:
            return
        ref = self._selected
        self._rotations[ref] = (self._rotations.get(ref, 0.0) + 180) % 360
        self._after_rotate(ref)

    def _rotate_reset(self):
        """Reset selected component rotation to 0°."""
        if not self._selected:
            return
        ref = self._selected
        self._rotations[ref] = 0.0
        self._after_rotate(ref)

    def _after_rotate(self, ref: str):
        """Shared post-rotation: clamp position, redraw, update info."""
        # Re-clamp inside board with new rotated size
        cx, cy = self._positions.get(ref, (10, 10))
        ow, oh = self._sizes.get(ref, (6, 4))
        rot = self._rotations.get(ref, 0.0)
        w, h = (oh, ow) if rot in (90, 270) else (ow, oh)
        cx = max(w / 2, min(self._board_w - w / 2, cx))
        cy = max(h / 2, min(self._board_h - h / 2, cy))
        self._positions[ref] = (cx, cy)
        self._full_redraw()
        self._update_sel_info(ref)

    def _update_sel_info(self, ref: str):
        """Update info label and rotation indicator for selected component."""
        comp = self._comp_map.get(ref)
        rot = self._rotations.get(ref, 0.0)
        rot_str = f"{int(rot)}°"
        if comp:
            self._info_lbl.config(
                text=f"Seçili: {ref}  ({comp.value})  [{comp.footprint}]  🔄 {rot_str}")
        self._rot_lbl.config(text=f"Açı: {rot_str}")

    # ── fallback placement ─────────────────────────────────────
    def _simple_grid_place(self):
        if not self._plan:
            return
        cols = max(1, int(math.ceil(math.sqrt(len(self._plan.components)))))
        for i, c in enumerate(self._plan.components):
            row, col = divmod(i, cols)
            w, h = self._sizes.get(c.ref, (6, 4))
            x = 8 + col * (w + 6)
            y = 8 + row * (h + 6)
            self._positions[c.ref] = (x, y)
