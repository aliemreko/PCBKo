"""
PCBKo — Modern Qt Interface
A refreshed, Apple-inspired application shell with a polished dark theme,
focused workflows, and fast generation controls.
"""
from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QRunnable, QThreadPool, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QCheckBox,
    QMessageBox,
)


def _ai_exc_reason(exc: Exception) -> str:
    msg = str(exc).lower()
    if "api key" in msg or "authentication" in msg or "unauthorized" in msg:
        return "API key eksik/hatalı"
    if "model not exist" in msg or "model_not_found" in msg:
        return "Model bulunamadı"
    if "rate limit" in msg or "quota" in msg:
        return "İstek limiti aşıldı"
    if "400" in msg:
        return "API 400 hatası"
    if "connection" in msg or "timeout" in msg:
        return "Bağlantı hatası"
    return str(exc)[:80]


class WorkerSignals(QObject):
    progress = Signal(int)
    status = Signal(str, str)
    log = Signal(str)
    step = Signal(int, str)
    finished = Signal(bool, str)


class GenerateWorker(QRunnable):
    def __init__(self, spec: dict, output_dir: Path, run_checks: bool, signals: WorkerSignals):
        super().__init__()
        self.spec = spec
        self.output_dir = output_dir
        self.run_checks = run_checks
        self.signals = signals

    def run(self) -> None:
        try:
            from .models import ProjectSpec
            from .component_autofill import autofill_components
            from .plan_normalizer import normalize_plan
            from .kicad_generator import write_kicad_compatible_outputs
            from .layout_router import compute_initial_placement

            self.signals.step.emit(0, "running")
            self.signals.progress.emit(5)
            self.signals.log.emit("📄 Tasarım spesifikasyonu hazırlanıyor...")

            project_spec = ProjectSpec.model_validate(self.spec)
            out_dir = self.output_dir / self.spec["name"]
            out_dir.mkdir(parents=True, exist_ok=True)
            spec_path = out_dir / "project_spec.json"
            spec_path.write_text(json.dumps(self.spec, indent=2, ensure_ascii=False), encoding="utf-8")
            self.signals.log.emit(f"Spec kaydedildi: {spec_path}")

            plan = None
            try:
                from .config import Settings
                from .deepseek_agent import DeepSeekPcbAgent

                settings = Settings.from_env()
                self.signals.log.emit("🌐 DeepSeek API'ye bağlanılıyor...")
                agent = DeepSeekPcbAgent(settings)
                raw_plan = agent.create_design_plan(project_spec)
                plan = normalize_plan(autofill_components(raw_plan))
            except Exception as exc:
                reason = _ai_exc_reason(exc)
                self.signals.log.emit(f"⚠️ AI plan hatası ({reason}) — yerel plandan yükleme denenecek...")
                plan = self._load_existing_plan(out_dir)
                if plan is None:
                    self.signals.log.emit("❌ Offline plan bulunamadı. Üretim durduruldu.")
                    self.signals.status.emit("Başarısız", "#f38ba8")
                    self.signals.finished.emit(False, "")
                    return

            plan_path = out_dir / "design_plan.json"
            plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            self.signals.step.emit(0, "done")
            self.signals.progress.emit(30)
            self.signals.log.emit("✅ Tasarım planı hazırlandı.")

            self.signals.step.emit(1, "running")
            files = write_kicad_compatible_outputs(plan, out_dir)
            self.signals.step.emit(1, "done")
            self.signals.progress.emit(50)
            self.signals.log.emit("✅ KiCad şema dosyaları üretildi.")

            board_width = float(self.spec.get("board_width", 50.0))
            board_height = float(self.spec.get("board_height", 40.0))
            self.signals.log.emit("📐 Otomatik yerleşim hesaplanıyor...")
            placements = compute_initial_placement(plan, board_width, board_height)
            self.signals.step.emit(2, "done")
            self.signals.progress.emit(60)
            self.signals.log.emit("✅ Yerleşim tamamlandı.")

            from .layout_outputs import write_layout_outputs_with_placements

            self.signals.step.emit(3, "running")
            self.signals.progress.emit(65)
            self.signals.log.emit("🔗 Routing başlatılıyor...")
            layout, layout_files = write_layout_outputs_with_placements(
                plan, placements, board_width, board_height, out_dir)
            self.signals.step.emit(3, "done")
            self.signals.progress.emit(85)
            self.signals.log.emit(
                f"✅ Routing tamamlandı: {int(layout.metrics.get('segment_count', 0))} segment, "
                f"{layout.metrics.get('total_trace_length_mm', 0):.1f} mm toplam yol")

            from .kicad_bootstrap import write_kicad_project_files
            self.signals.step.emit(4, "running")
            self.signals.log.emit("📦 KiCad proje dosyaları yazılıyor...")
            kicad_files = write_kicad_project_files(project_spec, out_dir)
            self.signals.step.emit(4, "done")
            self.signals.progress.emit(100)
            self.signals.log.emit("✅ PCB projesi başarıyla üretildi.")
            self.signals.status.emit("Tamamlandı!", "#a6e3a1")

            pcb_path = str(out_dir / f"{self.spec['name']}.kicad_pcb")
            self.signals.finished.emit(True, pcb_path)

        except Exception:
            tb = traceback.format_exc()
            self.signals.log.emit(f"❌ Hata oluştu:\n{tb}")
            self.signals.status.emit("Hata!", "#f38ba8")
            self.signals.finished.emit(False, "")

    def _load_existing_plan(self, out_dir: Path):
        from .models import DesignPlan
        from .component_autofill import autofill_components
        from .plan_normalizer import normalize_plan

        plan_path = out_dir / "design_plan.json"
        if not plan_path.exists():
            alt = Path("output") / self.spec["name"] / "design_plan.json"
            if alt.exists():
                plan_path = alt
        if plan_path.exists():
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            return normalize_plan(autofill_components(DesignPlan.model_validate(plan_data)))
        return None


class PCBModernGUI(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PCBKo")
        self.setMinimumSize(1040, 760)
        self._last_pcb_path = ""
        self._running = False
        self.thread_pool = QThreadPool()
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(18)

        self._side_panel = QFrame()
        self._side_panel.setObjectName("sidePanel")
        self._side_panel.setFixedWidth(260)
        side_layout = QVBoxLayout(self._side_panel)
        side_layout.setContentsMargins(20, 20, 20, 20)
        side_layout.setSpacing(18)

        app_label = QLabel("PCBKo")
        app_label.setObjectName("appTitle")
        app_label.setWordWrap(True)
        side_layout.addWidget(app_label)

        tagline = QLabel("Modern, Apple-esintili PCB üretim arayüzü")
        tagline.setWordWrap(True)
        tagline.setObjectName("tagline")
        side_layout.addWidget(tagline)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        for label in ["Tasarım", "Ayarlar", "İlerleme", "Hakkında"]:
            item = QListWidgetItem(label)
            item.setTextAlignment(Qt.AlignCenter)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)
        side_layout.addWidget(self.nav_list)
        side_layout.addStretch()

        root_layout.addWidget(self._side_panel)

        self._content_panel = QFrame()
        self._content_panel.setObjectName("contentPanel")
        self._content_panel.setStyleSheet("border-radius: 24px;")
        content_layout = QVBoxLayout(self._content_panel)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)

        header_layout = QHBoxLayout()
        title = QLabel("Modern PCB Akışı")
        title.setObjectName("pageTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.status_label = QLabel("Hazır")
        self.status_label.setObjectName("statusLabel")
        header_layout.addWidget(self.status_label)
        content_layout.addLayout(header_layout)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_design_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_progress_page())
        self.pages.addWidget(self._build_about_page())
        content_layout.addWidget(self.pages)

        footer_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        footer_layout.addWidget(self.progress_bar)

        self.open_button = QPushButton("KiCad'da Aç")
        self.open_button.setObjectName("secondaryButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_in_kicad)
        footer_layout.addWidget(self.open_button)

        self.generate_button = QPushButton("Üret")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self._on_generate)
        footer_layout.addWidget(self.generate_button)

        content_layout.addLayout(footer_layout)
        root_layout.addWidget(self._content_panel, 1)

        self.nav_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self._apply_styles()

    def _build_design_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(18)

        left_card = self._card_frame()
        left_layout = QVBoxLayout(left_card)
        left_layout.setSpacing(14)

        left_layout.addWidget(self._section_label("Proje Bilgisi"))
        self.project_name_input = QLineEdit("my_project")
        self.project_name_input.setPlaceholderText("Proje adı")
        left_layout.addWidget(self._field_row("Proje Adı", self.project_name_input))

        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Devrenizin ne yapmasını istediğinizi burada yazın...")
        self.description_input.setPlainText("5V girişle çalışan basit LED sürücü devresi, koruma dirençli")
        left_layout.addWidget(self._section_label("Açıklama"))
        left_layout.addWidget(self.description_input)

        self.constraints_input = QTextEdit()
        self.constraints_input.setPlaceholderText("Her satırda bir kısıtlama yazın...")
        self.constraints_input.setPlainText("2 katmanlı PCB\nMaliyet düşük olsun")
        left_layout.addWidget(self._section_label("Kısıtlamalar"))
        left_layout.addWidget(self.constraints_input)

        right_card = self._card_frame()
        right_layout = QVBoxLayout(right_card)
        right_layout.setSpacing(14)

        self.io_input = QTextEdit()
        self.io_input.setPlaceholderText("Giriş/Çıkış gereksinimlerini yazın...")
        self.io_input.setPlainText("VIN 5V\nGND\nLED çıkışı")
        right_layout.addWidget(self._section_label("Giriş/Çıkış"))
        right_layout.addWidget(self.io_input)

        self.parts_input = QTextEdit()
        self.parts_input.setPlaceholderText("Tercih ettiğiniz parçaları yazın...")
        self.parts_input.setPlainText("LM358\n2N3904\n1k resistor\nLED 0603")
        right_layout.addWidget(self._section_label("Tercih Edilen Parçalar"))
        right_layout.addWidget(self.parts_input)

        layout.addWidget(left_card, 2)
        layout.addWidget(right_card, 1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(18)

        settings_card = self._card_frame()
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setSpacing(16)

        settings_layout.addWidget(self._section_label("Boyutlar & Katman"))
        self.board_width_input = QSpinBox()
        self.board_width_input.setRange(10, 300)
        self.board_width_input.setValue(50)
        self.board_height_input = QSpinBox()
        self.board_height_input.setRange(10, 300)
        self.board_height_input.setValue(40)
        self.layer_count_input = QSpinBox()
        self.layer_count_input.setRange(1, 8)
        self.layer_count_input.setValue(2)
        size_layout = QHBoxLayout()
        size_layout.addWidget(self._field_row("Genişlik (mm)", self.board_width_input))
        size_layout.addWidget(self._field_row("Yükseklik (mm)", self.board_height_input))
        size_layout.addWidget(self._field_row("Katman", self.layer_count_input))
        settings_layout.addLayout(size_layout)

        settings_layout.addWidget(self._section_label("Trace & Spacing"))
        self.trace_clearance_input = QDoubleSpinBox()
        self.trace_clearance_input.setDecimals(2)
        self.trace_clearance_input.setSingleStep(0.05)
        self.trace_clearance_input.setRange(0.15, 1.0)
        self.trace_clearance_input.setValue(0.25)
        self.trace_width_input = QDoubleSpinBox()
        self.trace_width_input.setDecimals(2)
        self.trace_width_input.setSingleStep(0.05)
        self.trace_width_input.setRange(0.15, 1.0)
        self.trace_width_input.setValue(0.25)
        self.comp_spacing_input = QDoubleSpinBox()
        self.comp_spacing_input.setDecimals(1)
        self.comp_spacing_input.setSingleStep(0.5)
        self.comp_spacing_input.setRange(2.0, 15.0)
        self.comp_spacing_input.setValue(5.0)
        trace_layout = QHBoxLayout()
        trace_layout.addWidget(self._field_row("Trace Clearance", self.trace_clearance_input))
        trace_layout.addWidget(self._field_row("Trace Width", self.trace_width_input))
        trace_layout.addWidget(self._field_row("Komponent Aralığı", self.comp_spacing_input))
        settings_layout.addLayout(trace_layout)

        self.output_dir_input = QLineEdit(str(Path("output").resolve()))
        browse_button = QPushButton("Seç")
        browse_button.setObjectName("secondaryButton")
        browse_button.clicked.connect(self._browse_output)
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_dir_input)
        output_layout.addWidget(browse_button)
        settings_layout.addWidget(self._section_label("Çıktı Dizini"))
        settings_layout.addLayout(output_layout)

        self.run_checks_checkbox = QCheckBox("Üretim sonrası kontrolleri çalıştır (SKiDL, KiCad DRC)")
        settings_layout.addWidget(self.run_checks_checkbox)
        settings_layout.addWidget(QLabel("Apple-esintili arayüz ve hızlı üretim akışı için modern bir düzen."))

        layout.addWidget(settings_card)
        layout.addStretch()
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)

        steps_card = self._card_frame()
        steps_layout = QVBoxLayout(steps_card)
        steps_layout.setSpacing(10)
        steps_layout.addWidget(self._section_label("Üretim Adımları"))

        self.step_labels = []
        step_names = [
            "Tasarım planı üretiliyor",
            "KiCad şema dosyaları yazılıyor",
            "Otomatik yerleşim hesaplanıyor",
            "Routing yapılıyor",
            "KiCad proje dosyaları hazırlanıyor",
        ]
        for name in step_names:
            label = QLabel(f"⏳ {name}")
            label.setWordWrap(True)
            label.setStyleSheet("color: #a7b0d8;")
            steps_layout.addWidget(label)
            self.step_labels.append(label)

        layout.addWidget(steps_card)

        log_card = self._card_frame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setSpacing(10)
        log_layout.addWidget(self._section_label("Detaylı Üretim Günlüğü"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("Üretim ilerledikçe burada mesajlar görünecek...")
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_card, 1)

        return page

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        about_card = self._card_frame()
        about_layout = QVBoxLayout(about_card)
        about_layout.setSpacing(12)

        about_layout.addWidget(self._section_label("Neden Bu Arayüz?"))
        about_layout.addWidget(QLabel(
            "Yeni arayüz, Apple-esintili kart tasarım deneyimi sunar."
            " Tek adımda üretim, net ilerleme göstergeleri ve modern karanlık tema ile daha hızlı hissiyat."))
        about_layout.addWidget(self._section_label("Özellikler"))
        about_layout.addWidget(QLabel("• Tek tuşla PCB üretim akışı"))
        about_layout.addWidget(QLabel("• İçerik odaklı kart düzeni ve temiz tipografi"))
        about_layout.addWidget(QLabel("• Otomatik plan + routing + KiCad proje üretimi"))
        about_layout.addWidget(self._section_label("Kullanım"))
        about_layout.addWidget(QLabel(
            "Tasarım sekmesinde projenizi tanımlayın, Ayarlar'da parametreleri kontrol edin ve Üret'e basın."))

        layout.addWidget(about_card)
        layout.addStretch()
        return page

    def _card_frame(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        frame.setStyleSheet("border-radius: 22px; background: #181b28;")
        return frame

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 14px; color: #8da2ff; font-weight: 700;")
        return label

    def _field_row(self, label_text: str, widget: QWidget) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setStyleSheet("color: #b8c2ff;")
        layout.addWidget(label)
        layout.addWidget(widget)
        return row

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #12131a; color: #e8ecff; font-family: 'Segoe UI', 'San Francisco', sans-serif; }
            QFrame#sidePanel { background: #181b28; }
            QWidget#contentPanel { background: transparent; }
            QLabel#appTitle { color: #c7d2ff; font-size: 22px; font-weight: 700; }
            QLabel#statusLabel { color: #a6e3a1; font-size: 12px; }
            QListWidget#navList { background: transparent; border: none; }
            QListWidget#navList::item { margin: 6px 0; padding: 14px; border-radius: 16px; }
            QListWidget#navList::item:selected { background: #27305c; color: #ffffff; }
            QPushButton { border-radius: 16px; padding: 12px 18px; background: #232a3d; color: #e9efff; }
            QPushButton#primaryButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #5468ff, stop:1 #7da5ff); color: white; }
            QPushButton#primaryButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #6379ff, stop:1 #9cb4ff); }
            QPushButton#secondaryButton { background: transparent; border: 1px solid #3b4366; }
            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox { background: #161a28; border: 1px solid #2a3150; border-radius: 14px; padding: 10px; color: #eef1ff; }
            QProgressBar { background: #1d2337; border-radius: 10px; height: 16px; }
            QProgressBar::chunk { background: #7a96ff; border-radius: 10px; }
            """
        )

    def _browse_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Çıktı Dizini Seçin", str(Path("output").resolve()))
        if directory:
            self.output_dir_input.setText(directory)

    def _set_status(self, text: str, colour: str = "#a6e3a1") -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {colour}; font-weight: 600;")

    def _append_log(self, message: str) -> None:
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _set_step(self, index: int, state: str) -> None:
        icons = {"pending": "⏳", "running": "🔄", "done": "✅", "error": "❌"}
        colours = {"pending": "#a7b0d8", "running": "#89b4fa", "done": "#a6e3a1", "error": "#f38ba8"}
        if 0 <= index < len(self.step_labels):
            text = self.step_labels[index].text().split(" ", 1)[-1]
            self.step_labels[index].setText(f"{icons.get(state, '⏳')} {text}")
            self.step_labels[index].setStyleSheet(f"color: {colours.get(state, '#a7b0d8')};")

    def _reset_steps(self) -> None:
        for index, label in enumerate(self.step_labels):
            label.setText(label.text().split(" ", 1)[-1])
            label.setStyleSheet("color: #a7b0d8;")

    def _on_generate(self) -> None:
        if self._running:
            return
        self._running = True
        self.generate_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self._set_status("Üretiliyor...", "#89b4fa")
        self.progress_bar.setValue(0)
        self.log_text.clear()
        self._reset_steps()

        spec = self._build_spec()
        out_dir = Path(self.output_dir_input.text().strip() or str(Path("output").resolve()))
        signals = WorkerSignals()
        signals.progress.connect(self.progress_bar.setValue)
        signals.status.connect(self._set_status)
        signals.log.connect(self._append_log)
        signals.step.connect(self._set_step)
        signals.finished.connect(self._on_generation_finished)

        worker = GenerateWorker(spec, out_dir, self.run_checks_checkbox.isChecked(), signals)
        self.thread_pool.start(worker)
        self.nav_list.setCurrentRow(2)

    def _on_generation_finished(self, success: bool, pcb_path: str) -> None:
        self._running = False
        self.generate_button.setEnabled(True)
        if success and pcb_path:
            self._last_pcb_path = pcb_path
            self.open_button.setEnabled(True)
            self._append_log(f"PCB dosyası hazır: {pcb_path}")
            self._set_status("Tamamlandı!", "#a6e3a1")
        else:
            self._set_status("Başarısız", "#f38ba8")

    def _open_in_kicad(self) -> None:
        if not self._last_pcb_path:
            return
        pcb_path = self._last_pcb_path
        try:
            subprocess.Popen(["flatpak", "run", "org.kicad.KiCad", pcb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            try:
                subprocess.Popen(["kicad", pcb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                try:
                    subprocess.Popen(["pcbnew", pcb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except FileNotFoundError:
                    QMessageBox.critical(self, "Hata", "KiCad bulunamadı. Lütfen KiCad'ı yükleyin.")

    def _build_spec(self) -> dict:
        return {
            "name": self.project_name_input.text().strip() or "my_project",
            "description": self.description_input.toPlainText().strip(),
            "constraints": [line.strip() for line in self.constraints_input.toPlainText().splitlines() if line.strip()],
            "io_requirements": [line.strip() for line in self.io_input.toPlainText().splitlines() if line.strip()],
            "preferred_parts": [line.strip() for line in self.parts_input.toPlainText().splitlines() if line.strip()],
            "board_width": float(self.board_width_input.value()),
            "board_height": float(self.board_height_input.value()),
            "layer_count": int(self.layer_count_input.value()),
            "trace_clearance": float(self.trace_clearance_input.value()),
            "trace_width": float(self.trace_width_input.value()),
            "component_spacing": float(self.comp_spacing_input.value()),
        }


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = PCBModernGUI()
    window.show()
    sys.exit(app.exec())
