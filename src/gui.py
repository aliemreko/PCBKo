"""
PCBKo — Graphical User Interface
Tkinter-based GUI for designing PCBs with AI assistance.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from tkinter import (
    END,
    DISABLED,
    NORMAL,
    WORD,
    BooleanVar,
    DoubleVar,
    IntVar,
    StringVar,
    filedialog,
    messagebox,
)
import tkinter as tk
from tkinter import ttk


# ── Colour palette ──────────────────────────────────────────────
BG           = "#1e1e2e"
BG_DARKER    = "#181825"
BG_LIGHTER   = "#313244"
FG           = "#cdd6f4"
FG_DIM       = "#6c7086"
ACCENT       = "#89b4fa"
ACCENT_HOVER = "#74c7ec"
GREEN        = "#a6e3a1"
RED          = "#f38ba8"
YELLOW       = "#f9e2af"
ORANGE       = "#fab387"
SURFACE0     = "#313244"
SURFACE1     = "#45475a"
SURFACE2     = "#585b70"


def _ai_exc_reason(exc: Exception) -> str:
    """API hatasından kısa, okunabilir neden üretir."""
    msg = str(exc).lower()
    if "api key" in msg or "authentication" in msg or "unauthorized" in msg:
        return "API key eksik/hatalı"
    if "model not exist" in msg or "model_not_found" in msg:
        return "model bulunamadı"
    if "rate limit" in msg or "quota" in msg:
        return "istek limiti aşıldı"
    if "400" in msg:
        return "API 400 hatası"
    if "connection" in msg or "timeout" in msg:
        return "bağlantı hatası"
    # ilk 80 karakter yeterli
    return str(exc)[:80]


class PCBAgentGUI:
    """Main application window."""

    # ── Construction ────────────────────────────────────────────
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("PCBKo")
        self.root.geometry("1100x780")
        self.root.minsize(900, 650)
        self.root.configure(bg=BG)
        self.root.option_add("*TCombobox*Listbox.background", BG_LIGHTER)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)

        self._configure_styles()

        # ── State variables ─────────────────────────────────────
        self.project_name  = StringVar(value="my_project")
        self.description   = StringVar(value="")
        self.board_width   = DoubleVar(value=50.0)
        self.board_height  = DoubleVar(value=40.0)
        self.layer_count   = IntVar(value=2)
        self.trace_clearance = DoubleVar(value=0.25)
        self.trace_width   = DoubleVar(value=0.25)
        self.comp_spacing  = DoubleVar(value=5.0)
        self.run_checks_var = BooleanVar(value=False)
        self.output_dir    = StringVar(value=str(Path("output").resolve()))

        self._running = False
        self._thread: threading.Thread | None = None

        # ── Phase-1 results (plan) used by Phase-2 (routing) ───
        self._current_plan = None          # DesignPlan
        self._current_out_dir: Path | None = None
        self._current_spec = None          # ProjectSpec
        self._current_files: dict = {}

        self._build_ui()

    # ── Styling ─────────────────────────────────────────────────
    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=BG, foreground=FG, fieldbackground=BG_LIGHTER,
                         borderwidth=0, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=BG_LIGHTER)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground=ACCENT)
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"), foreground=ACCENT)
        style.configure("Dim.TLabel", foreground=FG_DIM, font=("Segoe UI", 9))
        style.configure("Status.TLabel", foreground=GREEN, font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", fieldbackground=BG_LIGHTER, foreground=FG,
                         insertcolor=ACCENT, borderwidth=1, relief="solid")
        style.configure("TSpinbox", fieldbackground=BG_LIGHTER, foreground=FG,
                         arrowcolor=ACCENT)
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG_DARKER,
                         font=("Segoe UI", 11, "bold"), padding=(20, 10))
        style.map("Accent.TButton",
                   background=[("active", ACCENT_HOVER), ("disabled", SURFACE2)])
        style.configure("Secondary.TButton", background=SURFACE1, foreground=FG,
                         font=("Segoe UI", 10), padding=(12, 6))
        style.map("Secondary.TButton",
                   background=[("active", SURFACE2), ("disabled", SURFACE0)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=SURFACE0, foreground=FG_DIM,
                         padding=(14, 6), font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                   background=[("selected", ACCENT)],
                   foreground=[("selected", BG_DARKER)])
        style.configure("Horizontal.TProgressbar", troughcolor=SURFACE0,
                         background=ACCENT, thickness=8)
        style.configure("TLabelframe", background=BG, foreground=ACCENT,
                         font=("Segoe UI", 10, "bold"))
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
        style.configure("Pass.TLabel",  background=BG_LIGHTER, foreground=GREEN,  font=("Segoe UI", 10, "bold"))
        style.configure("Fail.TLabel",  background=BG_LIGHTER, foreground=RED,    font=("Segoe UI", 10, "bold"))
        style.configure("Warn.TLabel",  background=BG_LIGHTER, foreground=YELLOW, font=("Segoe UI", 10, "bold"))
        style.configure("Info.TLabel",  background=BG_LIGHTER, foreground=ACCENT, font=("Segoe UI", 10, "bold"))

    # ── UI construction ─────────────────────────────────────────
    def _build_ui(self) -> None:
        # Title bar
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=20, pady=(14, 4))
        ttk.Label(top, text="⚡ PCBKo", style="Title.TLabel").pack(side="left")
        self.status_label = ttk.Label(top, text="Hazır", style="Status.TLabel")
        self.status_label.pack(side="right")

        # Notebook / Tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        self._tab_design     = ttk.Frame(self.notebook)
        self._tab_schematic  = ttk.Frame(self.notebook)
        self._tab_placement  = ttk.Frame(self.notebook)
        self._tab_settings   = ttk.Frame(self.notebook)
        self._tab_log        = ttk.Frame(self.notebook)
        self._tab_test       = ttk.Frame(self.notebook)
        self._tab_revision   = ttk.Frame(self.notebook)
        self.notebook.add(self._tab_design,     text="  Tasarım  ")
        self.notebook.add(self._tab_schematic,  text="  🧾 Şema  ")
        self.notebook.add(self._tab_placement,  text="  📐 Yerleşim  ")
        self.notebook.add(self._tab_settings,   text="  Ayarlar  ")
        self.notebook.add(self._tab_log,        text="  İlerleme  ")
        self.notebook.add(self._tab_test,       text="  🧪 Test & Simülasyon  ")
        self.notebook.add(self._tab_revision,   text="  🔄 PCB Revizyon  ")

        self._build_design_tab()
        self._build_schematic_tab()
        self._build_placement_tab()
        self._build_settings_tab()
        self._build_log_tab()
        self._build_test_tab()
        self._build_revision_tab()

        # Bottom bar
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=20, pady=(0, 14))
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=400,
                                         style="Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.btn_open = ttk.Button(bottom, text="KiCad'da Aç", style="Secondary.TButton",
                                    command=self._open_in_kicad, state=DISABLED)
        self.btn_open.pack(side="right", padx=(4, 0))
        self.btn_generate = ttk.Button(bottom, text="▶  Üret", style="Accent.TButton",
                                        command=self._on_generate)
        self.btn_generate.pack(side="right")

    # ── Design tab ──────────────────────────────────────────────
    def _build_design_tab(self) -> None:
        frame = self._tab_design
        pad = {"padx": 14, "pady": 6}

        # Project name
        row = ttk.Frame(frame); row.pack(fill="x", **pad)
        ttk.Label(row, text="Proje Adı").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.project_name, width=30).pack(side="left", fill="x", expand=True)

        # Description (multiline)
        ttk.Label(frame, text="Devre Açıklaması", style="Section.TLabel").pack(anchor="w", padx=14, pady=(10, 2))
        ttk.Label(frame, text="Devrenin ne yapmasını istediğinizi detaylı yazın", style="Dim.TLabel").pack(anchor="w", padx=14)
        self.desc_text = tk.Text(frame, height=6, bg=BG_LIGHTER, fg=FG,
                                  insertbackground=ACCENT, font=("Consolas", 11),
                                  relief="solid", bd=1, wrap=WORD, highlightthickness=0)
        self.desc_text.pack(fill="x", padx=14, pady=(4, 8))
        self.desc_text.insert("1.0", "5V girişle çalışan basit LED sürücü devresi, koruma dirençli")

        # Constraints
        ttk.Label(frame, text="Kısıtlamalar (her satır bir kısıt)", style="Section.TLabel").pack(anchor="w", padx=14, pady=(6, 2))
        self.constraints_text = tk.Text(frame, height=3, bg=BG_LIGHTER, fg=FG,
                                         insertbackground=ACCENT, font=("Consolas", 10),
                                         relief="solid", bd=1, wrap=WORD, highlightthickness=0)
        self.constraints_text.pack(fill="x", padx=14, pady=(2, 6))
        self.constraints_text.insert("1.0", "2 katmanlı PCB\nMaliyet düşük olsun")

        # IO Requirements
        ttk.Label(frame, text="Giriş/Çıkış Gereksinimleri", style="Section.TLabel").pack(anchor="w", padx=14, pady=(6, 2))
        self.io_text = tk.Text(frame, height=2, bg=BG_LIGHTER, fg=FG,
                                insertbackground=ACCENT, font=("Consolas", 10),
                                relief="solid", bd=1, wrap=WORD, highlightthickness=0)
        self.io_text.pack(fill="x", padx=14, pady=(2, 6))
        self.io_text.insert("1.0", "VIN 5V\nGND\nLED çıkışı")

        # Preferred Parts
        ttk.Label(frame, text="Tercih Edilen Parçalar", style="Section.TLabel").pack(anchor="w", padx=14, pady=(6, 2))
        self.parts_text = tk.Text(frame, height=2, bg=BG_LIGHTER, fg=FG,
                                   insertbackground=ACCENT, font=("Consolas", 10),
                                   relief="solid", bd=1, wrap=WORD, highlightthickness=0)
        self.parts_text.pack(fill="x", padx=14, pady=(2, 4))
        self.parts_text.insert("1.0", "LM358\n2N3904\n1k resistor\nLED 0603")

    # ── Placement canvas tab ────────────────────────────────────
    def _build_placement_tab(self) -> None:
        frame = self._tab_placement

        from .placement_canvas import PlacementCanvas

        self._canvas_widget = PlacementCanvas(
            frame, on_status=self._canvas_status)
        self._canvas_widget.pack(fill="both", expand=True)

        # Bottom bar — action buttons
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", padx=14, pady=(4, 8))

        self.btn_auto_place = ttk.Button(
            bottom, text="🔄 Otomatik Yerleştir",
            style="Secondary.TButton", command=self._on_auto_place)
        self.btn_auto_place.pack(side="left", padx=(0, 8))

        self.btn_route = ttk.Button(
            bottom, text="🔗  Yolları Çiz & PCB Üret",
            style="Accent.TButton", command=self._on_route, state=DISABLED)
        self.btn_route.pack(side="left")

        self._placement_status = ttk.Label(
            bottom, text="Henüz tasarım planı yok.", foreground=FG_DIM,
            font=("Segoe UI", 10))
        self._placement_status.pack(side="right", padx=8)

    def _build_schematic_tab(self) -> None:
        frame = self._tab_schematic
        from .schematic_canvas import SchematicCanvas

        self._schematic_widget = SchematicCanvas(
            frame, on_status=self._schematic_status)
        self._schematic_widget.pack(fill="both", expand=True)

    def _schematic_status(self, msg: str) -> None:
        self.status_label.configure(text=msg, foreground=ACCENT)

    def _canvas_status(self, msg: str) -> None:
        """Callback from the canvas widget — update placement status."""
        self._placement_status.configure(text=msg)

    def _on_auto_place(self) -> None:
        """Re-run auto-placement on the canvas."""
        self._canvas_widget.auto_place()
        self._canvas_widget.clear_routes()
        self._placement_status.configure(
            text="Otomatik yerleşim uygulandı. Bileşenleri sürükleyerek ayarlayın.",
            foreground=ACCENT)

    # ── Settings tab ────────────────────────────────────────────
    def _build_settings_tab(self) -> None:
        frame = self._tab_settings
        pad = {"padx": 14, "pady": 6}

        # Board dimensions
        ttk.Label(frame, text="Board Boyutları", style="Section.TLabel").pack(anchor="w", padx=14, pady=(12, 4))
        dim_row = ttk.Frame(frame); dim_row.pack(fill="x", **pad)
        ttk.Label(dim_row, text="Genişlik (mm)").pack(side="left", padx=(0, 4))
        ttk.Spinbox(dim_row, from_=10, to=300, increment=5, textvariable=self.board_width,
                     width=8).pack(side="left", padx=(0, 16))
        ttk.Label(dim_row, text="Yükseklik (mm)").pack(side="left", padx=(0, 4))
        ttk.Spinbox(dim_row, from_=10, to=300, increment=5, textvariable=self.board_height,
                     width=8).pack(side="left", padx=(0, 16))
        ttk.Label(dim_row, text="Katman").pack(side="left", padx=(0, 4))
        ttk.Spinbox(dim_row, from_=1, to=8, textvariable=self.layer_count,
                     width=4).pack(side="left")

        # Safe-zone / Clearance settings
        ttk.Label(frame, text="Güvenli Bölge Ayarları", style="Section.TLabel").pack(anchor="w", padx=14, pady=(18, 4))

        clr_row = ttk.Frame(frame); clr_row.pack(fill="x", **pad)
        ttk.Label(clr_row, text="Trace Clearance (mm)").pack(side="left", padx=(0, 4))
        self.clearance_spin = ttk.Spinbox(clr_row, from_=0.15, to=1.0, increment=0.05,
                                           textvariable=self.trace_clearance, width=8,
                                           format="%.2f")
        self.clearance_spin.pack(side="left", padx=(0, 16))
        ttk.Label(clr_row, text="Trace Width (mm)").pack(side="left", padx=(0, 4))
        ttk.Spinbox(clr_row, from_=0.15, to=1.0, increment=0.05,
                     textvariable=self.trace_width, width=8, format="%.2f").pack(side="left")

        sp_row = ttk.Frame(frame); sp_row.pack(fill="x", **pad)
        ttk.Label(sp_row, text="Komponent Aralığı (mm)").pack(side="left", padx=(0, 4))
        ttk.Spinbox(sp_row, from_=2.0, to=15.0, increment=0.5,
                     textvariable=self.comp_spacing, width=8, format="%.1f").pack(side="left")

        # Clearance visual indicator
        info = ttk.Frame(frame); info.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(info, text="ℹ️  Clearance >= 0.20mm önerilir. Komponent aralığı >= 4mm yeterlidir.",
                   style="Dim.TLabel").pack(anchor="w")

        # Output directory
        ttk.Label(frame, text="Çıktı Dizini", style="Section.TLabel").pack(anchor="w", padx=14, pady=(18, 4))
        out_row = ttk.Frame(frame); out_row.pack(fill="x", **pad)
        ttk.Entry(out_row, textvariable=self.output_dir, width=50).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(out_row, text="Gözat...", style="Secondary.TButton",
                    command=self._browse_output).pack(side="right")

        # Checks
        ttk.Checkbutton(frame, text="Üretim sonrası kontrolleri çalıştır (SKiDL, KiCad DRC)",
                         variable=self.run_checks_var).pack(anchor="w", padx=14, pady=(12, 4))

    # ── Log/Progress tab ────────────────────────────────────────
    def _build_log_tab(self) -> None:
        frame = self._tab_log

        # Step indicators
        self.steps_frame = ttk.Frame(frame)
        self.steps_frame.pack(fill="x", padx=14, pady=(12, 6))

        self.step_labels: list[ttk.Label] = []
        step_names = [
            "1. Tasarım planı üretiliyor",
            "2. KiCad şema dosyaları yazılıyor",
            "3. Komponent yerleşimi (kullanıcı)",
            "4. Yollar çiziliyor (routing)",
            "5. KiCad proje dosyaları üretiliyor",
        ]
        for i, name in enumerate(step_names):
            lbl = ttk.Label(self.steps_frame, text=f"⏳ {name}", foreground=FG_DIM,
                             font=("Segoe UI", 10))
            lbl.pack(anchor="w", pady=2)
            self.step_labels.append(lbl)

        # Log output
        ttk.Label(frame, text="Detaylı Log", style="Section.TLabel").pack(anchor="w", padx=14, pady=(10, 2))
        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(2, 8))
        self.log_text = tk.Text(log_frame, bg=BG_DARKER, fg=FG_DIM,
                                 font=("Consolas", 9), relief="flat", bd=0,
                                 wrap=WORD, highlightthickness=0, state=DISABLED)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    # ── Helpers ─────────────────────────────────────────────────
    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Çıktı Dizini Seçin")
        if path:
            self.output_dir.set(path)

    def _log(self, msg: str) -> None:
        self.log_text.configure(state=NORMAL)
        self.log_text.insert(END, msg + "\n")
        self.log_text.see(END)
        self.log_text.configure(state=DISABLED)

    def _set_step(self, index: int, state: str) -> None:
        """state: 'pending', 'running', 'done', 'error', 'skipped'"""
        icons = {"pending": "⏳", "running": "🔄", "done": "✅", "error": "❌", "skipped": "⏭️"}
        colours = {"pending": FG_DIM, "running": ACCENT, "done": GREEN, "error": RED, "skipped": YELLOW}
        if 0 <= index < len(self.step_labels):
            lbl = self.step_labels[index]
            old_text = lbl.cget("text")
            # Keep text after the first icon+space
            text_part = old_text.split(" ", 1)[-1] if " " in old_text else old_text
            lbl.configure(text=f"{icons.get(state, '⏳')} {text_part}",
                          foreground=colours.get(state, FG_DIM))

    def _set_progress(self, value: float) -> None:
        self.progress["value"] = value

    def _set_status(self, text: str, colour: str = GREEN) -> None:
        self.status_label.configure(text=text, foreground=colour)

    def _build_spec(self) -> dict:
        name = self.project_name.get().strip() or "my_project"
        desc = self.desc_text.get("1.0", END).strip()
        constraints = [c.strip() for c in self.constraints_text.get("1.0", END).strip().split("\n") if c.strip()]
        io_req = [c.strip() for c in self.io_text.get("1.0", END).strip().split("\n") if c.strip()]
        parts = [c.strip() for c in self.parts_text.get("1.0", END).strip().split("\n") if c.strip()]
        width = self.board_width.get()
        height = self.board_height.get()
        layers = self.layer_count.get()

        return {
            "name": name,
            "description": desc,
            "constraints": constraints,
            "io_requirements": io_req,
            "preferred_parts": parts,
            "board_outline": f"{width}mm x {height}mm",
            "layer_count": layers,
        }

    # ── Generate action ─────────────────────────────────────────
    def _on_generate(self) -> None:
        if self._running:
            return
        self._running = True
        self.btn_generate.configure(state=DISABLED)
        self.btn_open.configure(state=DISABLED)
        self.notebook.select(self._tab_log)

        # Reset steps
        for i in range(len(self.step_labels)):
            self._set_step(i, "pending")
        self._set_progress(0)
        self._set_status("Üretiliyor...", ACCENT)

        # Clear log
        self.log_text.configure(state=NORMAL)
        self.log_text.delete("1.0", END)
        self.log_text.configure(state=DISABLED)

        self._thread = threading.Thread(target=self._generate_worker, daemon=True)
        self._thread.start()

    def _generate_worker(self) -> None:
        """Phase 1: generate design plan, write schematic files, populate canvas."""
        try:
            spec_dict = self._build_spec()
            name = spec_dict["name"]
            out_dir = Path(self.output_dir.get()) / name
            out_dir.mkdir(parents=True, exist_ok=True)

            # Save spec
            spec_path = out_dir / "project_spec.json"
            spec_path.write_text(
                json.dumps(spec_dict, indent=2, ensure_ascii=False), encoding="utf-8")
            self.root.after(0, self._log, f"Spec kaydedildi: {spec_path}")

            # Step 1 — Design plan
            self.root.after(0, self._set_step, 0, "running")
            self.root.after(0, self._set_progress, 5)

            from .models import ProjectSpec, DesignPlan
            from .component_autofill import autofill_components
            from .plan_normalizer import normalize_plan
            from .kicad_generator import write_kicad_compatible_outputs
            from .layout_router import compute_initial_placement

            project_spec = ProjectSpec.model_validate(spec_dict)
            plan = None

            try:
                from .config import Settings
                from .deepseek_agent import DeepSeekPcbAgent

                self.root.after(0, self._log, "DeepSeek API'ye bağlanılıyor...")
                settings = Settings.from_env()
                agent = DeepSeekPcbAgent(settings)
                self.root.after(0, self._set_progress, 10)
                raw_plan = agent.create_design_plan(project_spec)
                plan = normalize_plan(autofill_components(raw_plan))
            except (ValueError, Exception) as e:
                if "DEEPSEEK_API_KEY" in str(e):
                    self.root.after(0, self._log,
                                    "⚠️  API key bulunamadı — offline plan aranıyor...")
                else:
                    reason = _ai_exc_reason(e)
                    self.root.after(0, self._log,
                                    f"⚠️  AI hatası ({reason}) — offline plan aranıyor...")

                # Try loading existing plan from disk
                plan_path = out_dir / "design_plan.json"
                if not plan_path.exists():
                    alt = Path("output") / name / "design_plan.json"
                    if alt.exists():
                        plan_path = alt
                        out_dir = alt.parent
                if plan_path.exists():
                    self.root.after(0, self._log,
                                    f"Mevcut plan yükleniyor: {plan_path}")
                    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                    plan = normalize_plan(
                        autofill_components(
                            DesignPlan.model_validate(plan_data)))
                else:
                    self.root.after(0, self._log,
                                    "❌ Offline plan bulunamadı.")
                    self.root.after(0, self._set_status, "Başarısız", RED)
                    return

            self.root.after(0, self._set_step, 0, "done")
            self.root.after(0, self._set_progress, 30)

            # Save plan
            plan_json_path = out_dir / "design_plan.json"
            plan_json_path.write_text(
                plan.model_dump_json(indent=2), encoding="utf-8")

            # Step 2 — KiCad schematic files
            self.root.after(0, self._set_step, 1, "running")
            self.root.after(0, self._log, "KiCad şema dosyaları yazılıyor...")
            files = write_kicad_compatible_outputs(plan, out_dir)
            self.root.after(0, self._set_step, 1, "done")
            self.root.after(0, self._set_progress, 50)

            # Compute initial auto-placement
            self.root.after(0, self._log,
                            "Otomatik yerleşim hesaplanıyor...")
            board_w = self.board_width.get()
            board_h = self.board_height.get()
            self._apply_safe_zone_settings()
            placements = compute_initial_placement(plan, board_w, board_h)
            initial_pos = {p.ref: (p.x_mm, p.y_mm) for p in placements}

            # Store for Phase 2
            self._current_plan = plan
            self._current_out_dir = out_dir
            self._current_spec = project_spec
            self._current_files = {k: str(v) for k, v in files.items()}

            # Step 3 — waiting for user placement
            self.root.after(0, self._set_step, 2, "running")
            self.root.after(0, self._set_progress, 60)
            self.root.after(0, self._set_status,
                            "Yerleşim bekleniyor…", YELLOW)

            # Populate canvas & switch tab
            self.root.after(
                0, self._populate_canvas, plan, board_w, board_h, initial_pos)
            self.root.after(0, self._log,
                            "\n📐 Bileşenler canvas'a yüklendi. "
                            "\"Yerleşim\" sekmesinde konumları ayarlayıp "
                            "\"🔗 Yolları Çiz\" butonuna basın.")

        except Exception:
            tb = traceback.format_exc()
            self.root.after(0, self._log, f"\n❌ Hata:\n{tb}")
            self.root.after(0, self._set_status, "Hata!", RED)
            for i in range(5):
                self.root.after(0, self._set_step, i, "error")

        finally:
            self._running = False
            self.root.after(0, lambda: self.btn_generate.configure(state=NORMAL))

    def _populate_canvas(self, plan, board_w, board_h, initial_pos):
        """Main-thread helper: load plan into the canvas and switch tab."""
        self._canvas_widget.load_plan(plan, board_w, board_h, initial_pos)
        if hasattr(self, "_schematic_widget"):
            self._schematic_widget.load_plan(plan)
        self.btn_route.configure(state=NORMAL)
        self._placement_status.configure(
            text=f"{len(plan.components)} bileşen yüklendi — sürükle & bırak ile yerleştirin.",
            foreground=ACCENT)
        self.notebook.select(self._tab_placement)

    # ── Phase 2: Route + PCB generation ─────────────────────────
    def _on_route(self) -> None:
        """User clicked 'Yolları Çiz' — start routing with current positions."""
        if self._running or self._current_plan is None:
            return
        self._running = True
        self.btn_route.configure(state=DISABLED)
        self.btn_generate.configure(state=DISABLED)
        self.btn_open.configure(state=DISABLED)
        self.notebook.select(self._tab_log)

        self.root.after(0, self._set_step, 2, "done")
        self.root.after(0, self._set_step, 3, "running")
        self.root.after(0, self._set_progress, 65)
        self.root.after(0, self._set_status, "Yollar çiziliyor...", ACCENT)
        self.root.after(0, self._log, "\n🔗 Routing başlatılıyor...")

        self._thread = threading.Thread(
            target=self._route_worker, daemon=True)
        self._thread.start()

    def _route_worker(self) -> None:
        """Phase 2 background thread: route + write KiCad outputs."""
        try:
            plan = self._current_plan
            out_dir = self._current_out_dir
            spec = self._current_spec
            name = spec.name

            # Get user placements from canvas
            placements = self._canvas_widget.get_placements()
            board_w = self.board_width.get()
            board_h = self.board_height.get()

            self._apply_safe_zone_settings()

            # Step 4 — routing
            self.root.after(0, self._log, "Yol çizimi (routing) hesaplanıyor...")
            from .layout_outputs import write_layout_outputs_with_placements
            layout, layout_files = write_layout_outputs_with_placements(
                plan, placements, board_w, board_h, out_dir)
            self._current_files.update(
                {k: str(v) for k, v in layout_files.items()})

            self.root.after(0, self._set_step, 3, "done")
            self.root.after(0, self._set_progress, 85)
            self.root.after(0, self._log,
                            f"Routing tamamlandı — "
                            f"{int(layout.metrics.get('segment_count', 0))} segment, "
                            f"{layout.metrics.get('total_trace_length_mm', 0):.1f} mm toplam iz")

            # Show routed traces on canvas
            self.root.after(
                0, self._canvas_widget.show_routes, layout.routed_nets)

            # Step 5 — KiCad project files
            self.root.after(0, self._set_step, 4, "running")
            self.root.after(0, self._log,
                            "KiCad proje dosyaları üretiliyor...")
            from .kicad_bootstrap import write_kicad_project_files
            kicad_files = write_kicad_project_files(spec, out_dir)
            self._current_files.update(
                {k: str(v) for k, v in kicad_files.items()})

            self.root.after(0, self._set_step, 4, "done")
            self.root.after(0, self._set_progress, 100)
            self.root.after(0, self._set_status, "Tamamlandı!", GREEN)
            self.root.after(0, self._log,
                            f"\n✅ Tüm dosyalar üretildi: {out_dir}")
            for key, fpath in self._current_files.items():
                self.root.after(0, self._log, f"  {key}: {fpath}")

            self._last_pcb_path = str(out_dir / f"{name}.kicad_pcb")
            self.root.after(0, lambda: self.btn_open.configure(state=NORMAL))
            self.root.after(0, self._placement_status.configure,
                            {"text": "✅ PCB üretildi!", "foreground": GREEN})

        except Exception:
            tb = traceback.format_exc()
            self.root.after(0, self._log, f"\n❌ Hata:\n{tb}")
            self.root.after(0, self._set_status, "Hata!", RED)

        finally:
            self._running = False
            self.root.after(0, lambda: self.btn_generate.configure(state=NORMAL))
            self.root.after(0, lambda: self.btn_route.configure(state=NORMAL))

    def _apply_safe_zone_settings(self) -> None:
        """Apply user-configured clearance/spacing values into the layout_router module."""
        try:
            from . import layout_router as lr

            lr._USER_COMP_SPACING = self.comp_spacing.get()
            lr._USER_TRACE_CLEARANCE = self.trace_clearance.get()
            lr._USER_TRACE_WIDTH = self.trace_width.get()
            # Map clearance to inflate radius: 1 cell per ~0.25mm
            lr._USER_INFLATE_RADIUS = max(1, int(round(self.trace_clearance.get() / 0.25)))
        except Exception:
            pass  # non-critical, defaults will be used

        try:
            from . import kicad_pcb_worker as kw
            kw._USER_TRACE_WIDTH = self.trace_width.get()
        except Exception:
            pass

    # ── Revision tab ─────────────────────────────────────────────
    def _build_revision_tab(self) -> None:
        frame = self._tab_revision

        # ── Header
        hdr = ttk.Frame(frame)
        hdr.pack(fill="x", padx=14, pady=(12, 2))
        ttk.Label(hdr, text="PCB Revizyonu", style="Section.TLabel").pack(side="left")
        ttk.Label(hdr,
                  text="Simülasyon bulgularına göre AI devreni iyileştirir ve yeniden üretir.",
                  style="Dim.TLabel").pack(side="left", padx=10)

        # ── Project dir row
        dir_row = ttk.Frame(frame)
        dir_row.pack(fill="x", padx=14, pady=(4, 2))
        ttk.Label(dir_row, text="Proje Dizini:").pack(side="left", padx=(0, 6))
        self.rev_dir_var = StringVar(value="")
        ttk.Entry(dir_row, textvariable=self.rev_dir_var, width=46).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(dir_row, text="Gözat...", style="Secondary.TButton",
                   command=self._browse_rev_dir).pack(side="right")
        ttk.Label(frame, text="Boş bırakırsanız, mevcut 'Proje Adı + Çıktı Dizini' kullanılır.",
                  style="Dim.TLabel").pack(anchor="w", padx=14)

        # ── Action buttons
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", padx=14, pady=(8, 4))
        self.btn_get_suggestions = ttk.Button(
            btn_row, text="💡 Öneri Al", style="Secondary.TButton",
            command=self._on_get_suggestions,
        )
        self.btn_get_suggestions.pack(side="left", padx=(0, 8))
        self.btn_apply_revision = ttk.Button(
            btn_row, text="✅ Seçili Önerileri Uygula & Yeniden Üret",
            style="Accent.TButton",
            command=self._on_apply_revision,
            state=DISABLED,
        )
        self.btn_apply_revision.pack(side="left")
        self.rev_status_lbl = ttk.Label(btn_row, text="", foreground=FG_DIM,
                                        font=("Segoe UI", 9))
        self.rev_status_lbl.pack(side="right")

        # ── Paned: sol = öneriler, sağ = log
        paned = tk.PanedWindow(frame, orient="horizontal", bg=BG,
                               sashwidth=6, sashrelief="flat")
        paned.pack(fill="both", expand=True, padx=14, pady=(4, 0))

        # Sol: öneri listesi
        left = ttk.Frame(paned)
        paned.add(left, minsize=280)

        ttk.Label(left, text="AI Önerileri (seçip uygulayabilirsiniz)",
                  style="Section.TLabel").pack(anchor="w", pady=(6, 4))
        ttk.Label(left, text="Kutuları işaretleyerek hangi düzeltmelerin yapılacağını seçin.",
                  style="Dim.TLabel").pack(anchor="w")

        sug_outer = ttk.Frame(left)
        sug_outer.pack(fill="both", expand=True, pady=(4, 0))
        sug_canvas = tk.Canvas(sug_outer, bg=BG, bd=0, highlightthickness=0)
        sug_vsb    = ttk.Scrollbar(sug_outer, orient="vertical", command=sug_canvas.yview)
        sug_canvas.configure(yscrollcommand=sug_vsb.set)
        sug_vsb.pack(side="right", fill="y")
        sug_canvas.pack(side="left", fill="both", expand=True)
        self._sug_inner  = ttk.Frame(sug_canvas)
        self._sug_win    = sug_canvas.create_window((0, 0), window=self._sug_inner, anchor="nw")
        self._sug_canvas = sug_canvas
        self._sug_vars: list[BooleanVar] = []
        self._sug_items: list[str] = []

        def _sug_inner_cfg(e):
            sug_canvas.configure(scrollregion=sug_canvas.bbox("all"))
            sug_canvas.itemconfig(self._sug_win, width=sug_canvas.winfo_width())
        def _sug_canvas_cfg(e):
            sug_canvas.itemconfig(self._sug_win, width=e.width)
        self._sug_inner.bind("<Configure>", _sug_inner_cfg)
        sug_canvas.bind("<Configure>", _sug_canvas_cfg)

        # Sağ: ilerleme logu + revizyon geçmişi
        right = ttk.Frame(paned)
        paned.add(right, minsize=320)

        ttk.Label(right, text="İşlem Logu", style="Section.TLabel").pack(anchor="w", pady=(6, 2))
        self.rev_log = tk.Text(right, height=10, bg=BG_DARKER, fg=FG_DIM,
                               font=("Consolas", 9), relief="flat", bd=0,
                               wrap=WORD, highlightthickness=0, state=DISABLED)
        rev_log_sb = ttk.Scrollbar(right, command=self.rev_log.yview)
        self.rev_log.configure(yscrollcommand=rev_log_sb.set)
        rev_log_sb.pack(side="right", fill="y")
        self.rev_log.pack(fill="both", expand=True)

        # Geçmiş
        ttk.Label(right, text="Revizyon Geçmişi", style="Section.TLabel").pack(anchor="w", pady=(10, 2))
        ttk.Button(right, text="🔄 Geçmişi Yenile", style="Secondary.TButton",
                   command=self._refresh_rev_history).pack(anchor="w", pady=(0, 4))
        hist_frame = ttk.Frame(right)
        hist_frame.pack(fill="both", expand=True)
        self.rev_history_text = tk.Text(hist_frame, height=8, bg=BG_DARKER, fg=FG_DIM,
                                        font=("Consolas", 9), relief="flat", bd=0,
                                        wrap=WORD, highlightthickness=0, state=DISABLED)
        hist_sb = ttk.Scrollbar(hist_frame, command=self.rev_history_text.yview)
        self.rev_history_text.configure(yscrollcommand=hist_sb.set)
        hist_sb.pack(side="right", fill="y")
        self.rev_history_text.pack(fill="both", expand=True)

    # ── Revision helpers ────────────────────────────────────────────────
    def _browse_rev_dir(self) -> None:
        path = filedialog.askdirectory(title="Proje Dizini Seçin")
        if path:
            self.rev_dir_var.set(path)

    def _get_rev_project_dir(self) -> Path:
        custom = self.rev_dir_var.get().strip()
        if custom:
            return Path(custom)
        name = self.project_name.get().strip() or "my_project"
        return Path(self.output_dir.get()) / name

    def _rev_log(self, msg: str, colour: str | None = None) -> None:
        self.rev_log.configure(state=NORMAL)
        self.rev_log.insert(END, msg + "\n")
        self.rev_log.see(END)
        self.rev_log.configure(state=DISABLED)

    def _on_get_suggestions(self) -> None:
        self.btn_get_suggestions.configure(state=DISABLED)
        self.btn_apply_revision.configure(state=DISABLED)
        self.rev_status_lbl.configure(text="Öneriler alınıyor...", foreground=ACCENT)
        # Clear log
        self.rev_log.configure(state=NORMAL)
        self.rev_log.delete("1.0", END)
        self.rev_log.configure(state=DISABLED)
        threading.Thread(target=self._get_suggestions_worker, daemon=True).start()

    def _get_suggestions_worker(self) -> None:
        try:
            project_dir = self._get_rev_project_dir()
            self.root.after(0, self._rev_log, f"Proje dizini: {project_dir}")

            # Simüle et
            self.root.after(0, self._rev_log, "🔍 Simülasyon çalıştırılıyor...")
            from .circuit_simulator import simulate_project
            report = simulate_project(project_dir)
            self.root.after(0, self._rev_log,
                            f"Simülasyon: {report.overall} — {report.summary}")

            issues = [t for t in report.tests if t.status in ("FAIL", "WARN")]
            if not issues:
                self.root.after(0, self._rev_log,
                                "✅ Tasarım zaten tüm testleri geçiyor. Revizyon gerekmez.")
                self.root.after(0, self.rev_status_lbl.configure,
                                {"text": "Sorun yok!", "foreground": GREEN})
                self.root.after(0, lambda: self.btn_get_suggestions.configure(state=NORMAL))
                return

            for t in issues:
                self.root.after(0, self._rev_log, f"  ⚠️  [{t.status}] {t.name}: {t.detail[:120]}")

            # AI önerileri
            self.root.after(0, self._rev_log, "\n🤖 AI öneriler alınıyor...")
            try:
                from .config import Settings
                from .pcb_revisor import PCBRevisor
                settings = Settings.from_env()
                revisor  = PCBRevisor(settings)
                suggestions = revisor.get_suggestions(project_dir, report)
            except Exception as _ai_exc:
                reason = _ai_exc_reason(_ai_exc)
                self.root.after(0, self._rev_log,
                                f"⚠️  AI kullanılamıyor ({reason}) — kural tabanlı öneriler devreye giriyor...")
                suggestions = self._rule_based_suggestions(issues)

            self.root.after(0, self._show_suggestions, suggestions)
            self.root.after(0, self.rev_status_lbl.configure,
                            {"text": f"{len(suggestions)} öneri hazır", "foreground": YELLOW})
            self.root.after(0, lambda: self.btn_apply_revision.configure(state=NORMAL))

        except Exception:
            import traceback as _tb
            self.root.after(0, self._rev_log, f"❌ Hata:\n{_tb.format_exc()}")
            self.root.after(0, self.rev_status_lbl.configure,
                            {"text": "Hata!", "foreground": RED})
        finally:
            self.root.after(0, lambda: self.btn_get_suggestions.configure(state=NORMAL))

    def _rule_based_suggestions(self, issues) -> list[str]:
        sug = []
        for t in issues:
            n = t.name
            if "Güç Ray" in n:        sug.append("GND ve VCC netlerini devreye ekle")
            if "LED Akım"   in n:       sug.append("LED'e 220Ω seri direnç ekle (5V, 20mA için)")
            if "Decoupling"  in n:       sug.append("Her IC için 100nF decoupling kap ekle")
            if "Base Direnc" in n:       sug.append("Transistör base pinine 1kΩ direnç ekle")
            if "Açık Devre"  in n:       sug.append("Floating bileşenleri uygun netlere bağla")
            if "Net Bağ"     in n:       sug.append("Tek-pinli netleri tamamla veya kaldır")
        return sug if sug else ["Genel: GND/VCC netlerini ve koruma elemanlarını kontrol et"]

    def _show_suggestions(self, suggestions: list[str]) -> None:
        # Eski widgetları temizle
        for w in self._sug_inner.winfo_children():
            w.destroy()
        self._sug_vars.clear()
        self._sug_items.clear()

        if not suggestions:
            ttk.Label(self._sug_inner, text="Öneri bulunamadı.", style="Dim.TLabel").pack(anchor="w", padx=4)
            return

        for i, sug in enumerate(suggestions):
            var = BooleanVar(value=True)
            self._sug_vars.append(var)
            self._sug_items.append(sug)

            row = ttk.Frame(self._sug_inner, style="Card.TFrame")
            row.pack(fill="x", pady=2, padx=2)
            cb = tk.Checkbutton(
                row, variable=var, bg=BG_LIGHTER, fg=FG,
                selectcolor=BG_DARKER, activebackground=BG_LIGHTER,
                relief="flat", bd=0,
            )
            cb.pack(side="left", padx=(6, 2), pady=4)
            ttk.Label(
                row, text=f"{i+1}. {sug}",
                wraplength=240, justify="left",
                background=BG_LIGHTER, foreground=FG,
                font=("Segoe UI", 9),
            ).pack(side="left", padx=(0, 6), pady=4)

    def _on_apply_revision(self) -> None:
        selected = [item for var, item in zip(self._sug_vars, self._sug_items) if var.get()]
        if not selected:
            messagebox.showwarning("Uyarı", "Lütfen en az bir öneri seçin.")
            return
        self.btn_apply_revision.configure(state=DISABLED)
        self.btn_get_suggestions.configure(state=DISABLED)
        self.rev_status_lbl.configure(text="Revizyon uygulanıyor...", foreground=ACCENT)
        # Clear log
        self.rev_log.configure(state=NORMAL)
        self.rev_log.delete("1.0", END)
        self.rev_log.configure(state=DISABLED)
        threading.Thread(
            target=self._apply_revision_worker, args=(selected,), daemon=True
        ).start()

    def _apply_revision_worker(self, selected: list[str]) -> None:
        try:
            project_dir = self._get_rev_project_dir()
            spec_dict   = self._build_spec()
            from .models import ProjectSpec
            spec = ProjectSpec.model_validate(spec_dict)

            def _cb(msg: str):
                self.root.after(0, self._rev_log, msg)

            _cb(f"📁 Proje dizini: {project_dir}")
            _cb(f"📝 Seçilen {len(selected)} öneri uygulanacak:")
            for s in selected:
                _cb(f"   • {s}")
            _cb("")

            try:
                from .config import Settings
                from .pcb_revisor import PCBRevisor
                settings = Settings.from_env()
                revisor  = PCBRevisor(settings)
                self._apply_safe_zone_settings()
                result   = revisor.apply_revision(
                    project_dir, spec,
                    selected_suggestions=selected,
                    progress_cb=_cb,
                )
            except Exception as _ai_exc:
                reason = _ai_exc_reason(_ai_exc)
                _cb(f"⚠️  AI kullanılamıyor ({reason}) — kural tabanlı düzeltmeler uygulanıyor...")
                result = self._offline_revision(project_dir, spec, _cb)

            if result.success:
                self.root.after(0, self.rev_status_lbl.configure,
                                {"text": "✅ Revizyon tamamlandı!", "foreground": GREEN})
                self.root.after(0, self._refresh_rev_history)
                # PCB aç butonu aktif et
                if result.files:
                    pcb_candidates = [v for k, v in result.files.items() if "pcb" in k.lower()]
                    if pcb_candidates:
                        self._last_pcb_path = pcb_candidates[0]
                        self.root.after(0, lambda: self.btn_open.configure(state=NORMAL))
                if result.sim_before and result.sim_after:
                    _cb(f"\n📊 Sonuç: {result.sim_before.overall} → {result.sim_after.overall}")
                    _cb(f"   Öncesi: {result.sim_before.summary}")
                    _cb(f"   Sonrası: {result.sim_after.summary}")
            else:
                self.root.after(0, self.rev_status_lbl.configure,
                                {"text": "❌ Revizyon başarısız", "foreground": RED})
                _cb(f"❌ Hata: {result.error[:300]}")

        except Exception:
            import traceback as _tb
            self.root.after(0, self._rev_log, f"❌ Hata:\n{_tb.format_exc()}")
            self.root.after(0, self.rev_status_lbl.configure,
                            {"text": "Hata!", "foreground": RED})
        finally:
            self.root.after(0, lambda: self.btn_apply_revision.configure(state=NORMAL))
            self.root.after(0, lambda: self.btn_get_suggestions.configure(state=NORMAL))

    def _offline_revision(self, project_dir: Path, spec, cb) -> object:
        """API key olmadığında kural tabanlı revizyon."""
        import json, shutil
        from datetime import datetime
        from .pcb_revisor import apply_rule_based_fixes, load_revision_log, save_revision_log, RevisionResult
        from .circuit_simulator import simulate_project
        from .component_autofill import autofill_components
        from .kicad_bootstrap import write_kicad_project_files
        from .kicad_generator import write_kicad_compatible_outputs
        from .layout_outputs import write_layout_outputs
        from .models import DesignPlan
        from .plan_normalizer import normalize_plan

        result           = RevisionResult()
        result.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log              = load_revision_log(project_dir)
        result.revision_id = f"Rev{len(log)+1:03d}"
        result.sim_before  = simulate_project(project_dir)

        plan_path = project_dir / "design_plan.json"
        if not plan_path.exists():
            result.error = "design_plan.json bulunamadı."
            return result

        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        issues = [f"[{t.status}] {t.name}: {t.detail}" for t in result.sim_before.tests if t.status in ("FAIL","WARN")]
        fixed, changes = apply_rule_based_fixes(plan_data, issues)
        result.changes_applied = changes
        for c in changes:
            cb(f"   ✓ {c}")

        # Yedekle
        backup_dir = project_dir / "revisions" / result.revision_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in project_dir.iterdir():
            if f.is_file(): shutil.copy2(f, backup_dir / f.name)

        plan_path.write_text(json.dumps(fixed, indent=2, ensure_ascii=False), encoding="utf-8")
        self._apply_safe_zone_settings()
        plan_obj = normalize_plan(autofill_components(DesignPlan.model_validate(fixed)))
        result.new_plan = plan_obj
        files = write_kicad_compatible_outputs(plan_obj, project_dir)
        files.update(write_layout_outputs(plan_obj, spec, project_dir))
        files.update(write_kicad_project_files(spec, project_dir))
        result.files = {k: str(v) for k, v in files.items()}
        result.sim_after = simulate_project(project_dir)
        result.success = True
        log.append(result.to_dict())
        save_revision_log(project_dir, log)
        return result

    def _refresh_rev_history(self) -> None:
        project_dir = self._get_rev_project_dir()
        from .pcb_revisor import load_revision_log
        try:
            log = load_revision_log(project_dir)
        except Exception:
            log = []

        self.rev_history_text.configure(state=NORMAL)
        self.rev_history_text.delete("1.0", END)
        if not log:
            self.rev_history_text.insert(END, "Henüz revizyon yapılmadı.\n")
        else:
            for entry in reversed(log):
                rid    = entry.get("revision_id", "?")
                ts     = entry.get("timestamp", "")
                ok     = "✅" if entry.get("success") else "❌"
                before = entry.get("sim_before_overall", "?")
                after  = entry.get("sim_after_overall",  "?")
                self.rev_history_text.insert(
                    END,
                    f"{ok} {rid}  [{ts}]  {before} → {after}\n"
                )
                changes = entry.get("changes_applied", [])
                for c in changes:
                    self.rev_history_text.insert(END, f"   • {c}\n")
                self.rev_history_text.insert(END, "\n")
        self.rev_history_text.configure(state=DISABLED)

    # ── Test & Simulation tab ───────────────────────────────────
    def _build_test_tab(self) -> None:
        frame = self._tab_test

        # Header
        hdr = ttk.Frame(frame)
        hdr.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(hdr, text="PCB Devre Simülasyonu", style="Section.TLabel").pack(side="left")
        self.btn_simulate = ttk.Button(
            hdr, text="▶  Simülasyonu Çalıştır", style="Accent.TButton",
            command=self._on_simulate,
        )
        self.btn_simulate.pack(side="right")

        # Project dir selector
        dir_row = ttk.Frame(frame)
        dir_row.pack(fill="x", padx=14, pady=(0, 6))
        ttk.Label(dir_row, text="Proje Dizini:").pack(side="left", padx=(0, 6))
        self.sim_dir_var = StringVar(value="")
        ttk.Entry(dir_row, textvariable=self.sim_dir_var, width=48).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(dir_row, text="Gözat...", style="Secondary.TButton",
                   command=self._browse_sim_dir).pack(side="right")
        ttk.Label(frame,
                  text="Boş bırakırsanız, arayüzdeki 'Proje Adı' ve 'Çıktı Dizini' kullanılır.",
                  style="Dim.TLabel").pack(anchor="w", padx=14)

        # Overall verdict banner
        self.sim_verdict = ttk.Label(
            frame, text="—  Henüz test çalıştırılmadı  —",
            font=("Segoe UI", 12, "bold"), foreground=FG_DIM, background=BG_LIGHTER,
            anchor="center",
        )
        self.sim_verdict.pack(fill="x", padx=14, pady=(10, 4), ipady=8)

        # Summary line
        self.sim_summary_lbl = ttk.Label(frame, text="", style="Dim.TLabel")
        self.sim_summary_lbl.pack(anchor="w", padx=14)

        # Scrollable results area
        results_outer = ttk.Frame(frame)
        results_outer.pack(fill="both", expand=True, padx=14, pady=(6, 10))

        canvas = tk.Canvas(results_outer, bg=BG, bd=0, highlightthickness=0)
        vsb = ttk.Scrollbar(results_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._sim_inner = ttk.Frame(canvas)
        self._sim_canvas_window = canvas.create_window((0, 0), window=self._sim_inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(self._sim_canvas_window, width=canvas.winfo_width())

        def _on_canvas_configure(event):
            canvas.itemconfig(self._sim_canvas_window, width=event.width)

        self._sim_inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))
        canvas.bind_all("<Button-4>",   lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>",   lambda e: canvas.yview_scroll( 1, "units"))

        self._sim_canvas = canvas

    def _browse_sim_dir(self) -> None:
        path = filedialog.askdirectory(title="Proje Dizini Seçin")
        if path:
            self.sim_dir_var.set(path)

    def _on_simulate(self) -> None:
        self.btn_simulate.configure(state=DISABLED)
        self.notebook.select(self._tab_test)
        threading.Thread(target=self._run_simulation_worker, daemon=True).start()

    def _run_simulation_worker(self) -> None:
        try:
            # Determine project dir
            custom = self.sim_dir_var.get().strip()
            if custom:
                project_dir = Path(custom)
            else:
                name = self.project_name.get().strip() or "my_project"
                project_dir = Path(self.output_dir.get()) / name

            self.root.after(0, self._sim_verdict_set,
                            "⏳  Simülasyon çalışıyor...", ACCENT)
            self.root.after(0, self.sim_summary_lbl.configure, {"text": f"Dizin: {project_dir}"})

            from .circuit_simulator import simulate_project
            report = simulate_project(project_dir)

            self.root.after(0, self._sim_show_results, report)
        except Exception:
            import traceback as _tb
            msg = _tb.format_exc()
            self.root.after(0, self._sim_verdict_set, "❌  Simülasyon hatası", RED)
            self.root.after(0, self.sim_summary_lbl.configure, {"text": msg[:200]})
        finally:
            self.root.after(0, lambda: self.btn_simulate.configure(state=NORMAL))

    def _sim_verdict_set(self, text: str, colour: str) -> None:
        self.sim_verdict.configure(text=text, foreground=colour)

    def _sim_show_results(self, report) -> None:
        # Verdict banner
        colours = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW, "UNKNOWN": FG_DIM}
        icons   = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "UNKNOWN": "❓"}
        ov = report.overall
        self._sim_verdict_set(
            f"{icons.get(ov,'❓')}  {ov}  —  Devre {'ÇALIŞIR' if ov=='PASS' else 'SORUNLU' if ov=='WARN' else 'ÇALIŞMAZ' if ov=='FAIL' else '?'}",
            colours.get(ov, FG_DIM),
        )
        self.sim_summary_lbl.configure(text=report.summary)

        # Clear old results
        for w in self._sim_inner.winfo_children():
            w.destroy()

        style_map = {"PASS": "Pass.TLabel", "FAIL": "Fail.TLabel",
                     "WARN": "Warn.TLabel", "INFO": "Info.TLabel"}
        icon_map  = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️"}

        for t in report.tests:
            card = ttk.Frame(self._sim_inner, style="Card.TFrame")
            card.pack(fill="x", pady=3, padx=2)

            # Top row: icon + name + status badge
            top_row = ttk.Frame(card, style="Card.TFrame")
            top_row.pack(fill="x", padx=8, pady=(6, 2))
            icon = icon_map.get(t.status, "❓")
            ttk.Label(top_row, text=f"{icon}  {t.name}",
                      font=("Segoe UI", 10, "bold"),
                      background=BG_LIGHTER, foreground=FG).pack(side="left")
            ttk.Label(top_row, text=t.status,
                      style=style_map.get(t.status, "TLabel")).pack(side="right", padx=4)

            # Detail
            ttk.Label(card, text=t.detail, wraplength=700,
                      background=BG_LIGHTER, foreground=FG_DIM,
                      font=("Consolas", 9), justify="left").pack(
                          anchor="w", padx=16, pady=(0, 6))

    # ── Open in KiCad ───────────────────────────────────────────
    def _open_in_kicad(self) -> None:
        pcb_path = getattr(self, "_last_pcb_path", None)
        if not pcb_path or not Path(pcb_path).exists():
            messagebox.showwarning("Uyarı", "KiCad PCB dosyası bulunamadı.")
            return

        self._log(f"KiCad açılıyor: {pcb_path}")
        try:
            # Try flatpak first (common on Linux)
            subprocess.Popen(["flatpak", "run", "org.kicad.KiCad", pcb_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            try:
                subprocess.Popen(["kicad", pcb_path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                try:
                    subprocess.Popen(["pcbnew", pcb_path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except FileNotFoundError:
                    messagebox.showerror("Hata", "KiCad bulunamadı. Lütfen KiCad'ı yükleyin.")

    # ── Run ─────────────────────────────────────────────────────
    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = PCBAgentGUI()
    app.run()


if __name__ == "__main__":
    main()
