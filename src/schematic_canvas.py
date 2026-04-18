"""Interactive schematic canvas for visualising components and net connections."""
from __future__ import annotations

import math
import tkinter as tk
from typing import Callable

from .models import DesignPlan

_BG = "#1e1e2e"
_FG = "#cdd6f4"
_DIM = "#6c7086"
_ACCENT = "#89b4fa"
_BOARD = "#181825"
_CANVAS_BG = "#FFFFFF"  # KiCad White background
_KICAD_RED = "#840000"
_KICAD_GREEN = "#008400"
_KICAD_TEAL = "#008484"
_IC_FILL = "#FFFFE6"


class SchematicCanvas(tk.Frame):
    """Simple schematic view with clickable nets and components."""

    def __init__(
        self,
        parent: tk.Widget,
        on_status: Callable[[str], None] | None = None,
        **kw,
    ):
        super().__init__(parent, bg=_BG, **kw)
        self._on_status = on_status or (lambda _: None)

        self._plan: DesignPlan | None = None
        self._positions: dict[str, tuple[float, float]] = {}
        self._pin_layout: dict[str, list[tuple[str, float, float]]] = {}
        self._selected_net: str | None = None
        self._selected_comp: str | None = None

        self._scale = 10.0
        self._pan_x = 40.0
        self._pan_y = 40.0
        self._min_scale = 4.0
        self._max_scale = 28.0

        self._build_widgets()

    def _build_widgets(self) -> None:
        toolbar = tk.Frame(self, bg=_BOARD)
        toolbar.pack(fill="x", side="top")

        btn_kw = dict(bg="#313244", fg=_FG, relief="flat", bd=0,
                      padx=8, pady=4, font=("Segoe UI", 9))
        tk.Button(toolbar, text="↻ Şemayı Yenile", command=self._full_redraw, **btn_kw).pack(side="left", padx=2, pady=3)
        tk.Button(toolbar, text="🔎 Net Seç", command=self._focus_selected, **btn_kw).pack(side="left", padx=2, pady=3)

        self._info_lbl = tk.Label(
            toolbar, text="Şema yüklenmedi.", bg=_BOARD, fg=_ACCENT,
            font=("Segoe UI", 9), anchor="w")
        self._info_lbl.pack(side="right", padx=8)

        body = tk.Frame(self, bg=_BG)
        body.pack(fill="both", expand=True)

        self._cv = tk.Canvas(body, bg=_CANVAS_BG, highlightthickness=0, cursor="crosshair")
        self._cv.pack(side="left", fill="both", expand=True)
        self._cv.bind("<Button-1>", self._canvas_click)
        self._cv.bind("<MouseWheel>", self._wheel)
        self._cv.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        self._cv.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.15))

        side = tk.Frame(body, bg=_BG, width=240)
        side.pack(side="right", fill="y")

        tk.Label(side, text="Bağlantılar", bg=_BG, fg=_ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="nw", padx=12, pady=(12, 4))
        self._net_list = tk.Listbox(side, bg="#242438", fg=_FG,
                                    activestyle="none", selectbackground="#5b6eae",
                                    highlightthickness=0, bd=0)
        self._net_list.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self._net_list.bind("<<ListboxSelect>>", self._on_net_select)

        tk.Label(side, text="Detay", bg=_BG, fg=_ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="nw", padx=12, pady=(2, 4))
        self._detail_text = tk.Text(side, height=8, bg="#242438", fg=_FG,
                                    relief="flat", bd=0, highlightthickness=0,
                                    state="disabled", font=("Consolas", 10))
        self._detail_text.pack(fill="x", padx=12, pady=(0, 12))

    def load_plan(self, plan: DesignPlan) -> None:
        self._plan = plan
        self._selected_net = None
        self._selected_comp = None
        self._layout_components()
        self._populate_net_list()
        self._set_status(f"{len(plan.components)} bileşenli şema yüklendi.")
        self._full_redraw()

    def _set_status(self, msg: str) -> None:
        self._on_status(msg)

    def _layout_components(self) -> None:
        if not self._plan:
            return

        count = len(self._plan.components)
        cols = max(1, int(math.ceil(math.sqrt(count))))
        gap_x = 24.0
        gap_y = 18.0
        
        self._positions = {}
        self._pin_layout = {}
        self._comp_dims = {} # Store dimensions per component

        for idx, comp in enumerate(self._plan.components):
            row, col = divmod(idx, cols)
            
            # Determine component type and shape
            ref = comp.ref.upper()
            val = comp.value.lower()
            
            pin_count = 2
            comp_w, comp_h = 8.0, 4.0 # Defaults for passives
            
            if ref.startswith("U"):
                # IC
                if "555" in val or "358" in val: pin_count = 8
                elif "595" in val or "232" in val: pin_count = 16
                elif "328" in val: pin_count = 28
                elif "stm32" in val: pin_count = 48
                elif "esp32" in val: pin_count = 38
                else: pin_count = 8
                comp_w = 14.0
                comp_h = max(6.0, (pin_count // 2) * 2.5)
            elif ref.startswith("J") or ref.startswith("P"):
                # Connector
                import re
                m = re.search(r'0*1x0*([0-9]+)', val)
                if m: pin_count = int(m.group(1))
                else: pin_count = 4
                comp_w = 6.0
                comp_h = max(6.0, pin_count * 2.5)
            elif ref.startswith("Q"):
                # Transistor
                pin_count = 3
                comp_w = 6.0
                comp_h = 6.0
            
            x = 15.0 + col * (30.0 + gap_x)
            y = 15.0 + row * (20.0 + gap_y)
            self._positions[comp.ref] = (x, y)
            self._comp_dims[comp.ref] = (comp_w, comp_h)

            pins: list[tuple[str, float, float]] = []
            
            if ref.startswith("U"):
                # IC pins: half left, half right
                left_pins = math.ceil(pin_count / 2)
                right_pins = pin_count - left_pins
                for i in range(left_pins):
                    dy = (i - (left_pins - 1) / 2.0) * 2.0
                    pins.append((str(i + 1), -comp_w / 2.0, dy))
                for i in range(right_pins):
                    dy = (right_pins - 1 - i - (right_pins - 1) / 2.0) * 2.0
                    pins.append((str(pin_count - i), comp_w / 2.0, dy))
                    
            elif ref.startswith("J") or ref.startswith("P"):
                # Connector pins: all left
                for i in range(pin_count):
                    dy = (i - (pin_count - 1) / 2.0) * 2.0
                    pins.append((str(i + 1), -comp_w / 2.0, dy))
                    
            elif ref.startswith("Q"):
                # Transistor: B left, C top right, E bottom right
                pins.append(("1", -comp_w / 2.0, 0)) # Base or Gate
                pins.append(("2", comp_w / 2.0, -comp_h / 3.0)) # Collector or Drain
                pins.append(("3", comp_w / 2.0, comp_h / 3.0)) # Emitter or Source
                
            else:
                # Passives (R, C, D, L): pin 1 left, pin 2 right
                pins.append(("1", -comp_w / 2.0, 0))
                pins.append(("2", comp_w / 2.0, 0))
                
            self._pin_layout[comp.ref] = pins

    def _populate_net_list(self) -> None:
        self._net_list.delete(0, tk.END)
        if not self._plan:
            return
        for net in self._plan.nets:
            label = f"{net.net_name} ({len(net.nodes)})"
            self._net_list.insert(tk.END, label)

    def _full_redraw(self, event=None) -> None:
        self._cv.delete("all")
        if not self._plan:
            w = self._cv.winfo_width() or 600
            h = self._cv.winfo_height() or 400
            self._cv.create_text(
                w / 2, h / 2,
                text='Şema yüklenmedi. Önce "Üret" butonuna basın.',
                fill=_DIM, font=("Segoe UI", 14), tags="placeholder",
            )
            return
        self._draw_grid()
        self._draw_nets()
        for comp in self._plan.components:
            self._draw_comp(comp.ref)

    def _draw_grid(self) -> None:
        w = self._cv.winfo_width() or 800
        h = self._cv.winfo_height() or 600
        grid_size = 5.0 # mm
        px_step = grid_size * self._scale
        if px_step < 8:
            return # Avoid drawing too many dots when zoomed out
            
        start_x = (self._pan_x % px_step)
        start_y = (self._pan_y % px_step)
        
        for x in range(int(start_x), w, int(px_step)):
            for y in range(int(start_y), h, int(px_step)):
                self._cv.create_oval(x-1, y-1, x+1, y+1, fill="#C0C0C0", outline="", tags="grid")

    def _draw_comp(self, ref: str) -> None:
        if ref not in self._positions:
            return
        x, y = self._positions[ref]
        comp_w, comp_h = self._comp_dims.get(ref, (10.0, 6.0))
        
        x1, y1 = self._mm2px(x - comp_w / 2.0, y - comp_h / 2.0)
        x2, y2 = self._mm2px(x + comp_w / 2.0, y + comp_h / 2.0)
        selected = self._selected_comp == ref
        outline = _ACCENT if selected else _KICAD_RED
        width = 3 if selected else 2

        comp = next((c for c in self._plan.components if c.ref == ref), None)
        label = ref if comp is None else f"{comp.ref}\n{comp.value}"
        
        # Draw specific shapes based on prefix
        if ref.startswith("R"):
            # Resistor box
            self._cv.create_rectangle(x1, y1 + (y2-y1)*0.2, x2, y2 - (y2-y1)*0.2, outline=outline, fill=_IC_FILL, width=width, tags=(f"c_{ref}", "comp"))
        elif ref.startswith("C"):
            # Capacitor parallel lines
            cx, cy = (x1+x2)/2, (y1+y2)/2
            gap = 2.0 * self._scale
            self._cv.create_line(cx - gap/2, y1, cx - gap/2, y2, fill=outline, width=width, tags=(f"c_{ref}", "comp"))
            self._cv.create_line(cx + gap/2, y1, cx + gap/2, y2, fill=outline, width=width, tags=(f"c_{ref}", "comp"))
        elif ref.startswith("D"):
            # Diode/LED triangle
            cx, cy = (x1+x2)/2, (y1+y2)/2
            self._cv.create_polygon(x1, y1, x1, y2, x2, cy, fill=_IC_FILL, outline=outline, width=width, tags=(f"c_{ref}", "comp"))
            self._cv.create_line(x2, y1, x2, y2, fill=outline, width=width, tags=(f"c_{ref}", "comp"))
        elif ref.startswith("Q"):
            # Transistor circle
            self._cv.create_oval(x1, y1, x2, y2, outline=outline, fill=_IC_FILL, width=width, tags=(f"c_{ref}", "comp"))
        else:
            # IC / Connector Rectangle
            self._cv.create_rectangle(x1, y1, x2, y2, outline=outline, fill=_IC_FILL, width=width, tags=(f"c_{ref}", "comp"))

        self._cv.create_text(
            (x1 + x2) / 2, y1 - 10 if not ref.startswith("U") else (y1 + y2) / 2,
            text=label, fill=_KICAD_TEAL, font=("Consolas", 10, "bold"), tags=(f"c_{ref}",),
            justify="center"
        )

        for pin_name, dx, dy in self._pin_layout.get(ref, []):
            px, py = self._mm2px(x + dx, y + dy)
            sign = 1 if dx > 0 else -1
            line_len = 3.0 * self._scale
            end_x = px + sign * line_len
            
            self._cv.create_line(
                px, py, end_x, py,
                fill=_KICAD_RED, width=2, tags=(f"c_{ref}", "pin_line")
            )
            r = 2.0
            self._cv.create_oval(
                end_x - r, py - r, end_x + r, py + r,
                outline=_KICAD_RED, fill="", width=1.5, tags=(f"c_{ref}", "pin"),
            )
            if self._scale > 8:
                self._cv.create_text(
                    end_x + sign * 4, py, text=pin_name, fill=_KICAD_RED,
                    font=("Consolas", 8), anchor="w" if sign > 0 else "e", tags=(f"c_{ref}",),
                )

    def _draw_nets(self) -> None:
        if not self._plan:
            return
        for idx, net in enumerate(self._plan.nets):
            color = _KICAD_GREEN
            active = self._selected_net == net.net_name
            width = 3 if active else 2
            dash = ()
            points: list[tuple[float, float]] = []
            for node in net.nodes:
                if "." not in node:
                    continue
                ref, pin = node.split(".", 1)
                if ref not in self._positions:
                    continue
                pos = self._positions[ref]
                pin_pos = next(((dx, dy) for pname, dx, dy in self._pin_layout.get(ref, []) if pname == pin), None)
                if pin_pos is None:
                    pin_pos = (-5.0, 0.0)
                sign = 1 if pin_pos[0] > 0 else -1
                actual_x = pos[0] + pin_pos[0] + sign * 3.0
                actual_y = pos[1] + pin_pos[1]
                points.append((actual_x, actual_y))
            if len(points) < 2:
                continue
            base_x, base_y = points[0]
            for x, y in points[1:]:
                px1, py1 = self._mm2px(base_x, base_y)
                px2, py2 = self._mm2px(x, y)
                mid_x = (px1 + px2) / 2.0
                self._cv.create_line(
                    px1, py1, mid_x, py1, mid_x, py2, px2, py2,
                    fill=color, width=width, dash=dash,
                    tags=(f"net_{net.net_name}", "netline"),
                )
                base_x, base_y = x, y

    def _canvas_click(self, event: tk.Event) -> None:
        item = self._cv.find_closest(event.x, event.y)
        tags = self._cv.gettags(item)
        for tag in tags:
            if tag.startswith("c_"):
                self._selected_comp = tag[2:]
                self._selected_net = None
                self._update_details()
                self._full_redraw()
                return
        self._selected_comp = None
        self._selected_net = None
        self._update_details()
        self._full_redraw()

    def _on_net_select(self, event: tk.Event) -> None:
        selection = self._net_list.curselection()
        if not selection:
            self._selected_net = None
        else:
            idx = selection[0]
            self._selected_net = self._plan.nets[idx].net_name if self._plan else None
            self._selected_comp = None
        self._update_details()
        self._full_redraw()

    def _update_details(self) -> None:
        text = []
        if self._selected_net and self._plan:
            net = next((n for n in self._plan.nets if n.net_name == self._selected_net), None)
            if net:
                text.append(f"Net: {net.net_name}")
                text.append(f"Bağlı düğümler: {len(net.nodes)}")
                text.extend(net.nodes)
        elif self._selected_comp and self._plan:
            comp = next((c for c in self._plan.components if c.ref == self._selected_comp), None)
            if comp:
                text.append(f"Komponent: {comp.ref}")
                text.append(f"Değer: {comp.value}")
                text.append(f"Footprint: {comp.footprint}")
                related = [node for net in self._plan.nets for node in net.nodes if node.startswith(f"{comp.ref}.")]
                if related:
                    text.append("Bağlı netler:")
                    text.extend(related)
        else:
            text.append("Şema bileşeni seçmek için kutuya tıklayın.")
            text.append("Bağlantı seçmek için net listesinde tıklayın.")

        self._detail_text.configure(state="normal")
        self._detail_text.delete("1.0", tk.END)
        self._detail_text.insert(tk.END, "\n".join(text))
        self._detail_text.configure(state="disabled")

    def _focus_selected(self) -> None:
        if self._selected_net and self._plan:
            self._set_status(f"Seçili net: {self._selected_net}")
        elif self._selected_comp:
            self._set_status(f"Seçili komponent: {self._selected_comp}")
        else:
            self._set_status("Hiçbir eleman seçili değil.")

    def _mm2px(self, x: float, y: float) -> tuple[float, float]:
        return self._pan_x + x * self._scale, self._pan_y + y * self._scale

    def _zoom_at(self, px: float, py: float, factor: float) -> None:
        new_scale = max(self._min_scale, min(self._max_scale, self._scale * factor))
        if new_scale == self._scale:
            return
        mx = (px - self._pan_x) / self._scale
        my = (py - self._pan_y) / self._scale
        self._scale = new_scale
        self._pan_x = px - mx * new_scale
        self._pan_y = py - my * new_scale
        self._full_redraw()

    def _wheel(self, event: tk.Event) -> None:
        if event.delta > 0:
            self._zoom_at(event.x, event.y, 1.15)
        else:
            self._zoom_at(event.x, event.y, 1 / 1.15)
