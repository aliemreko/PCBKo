"""
AI Visual Review Engine
=======================
Captures the schematic/PCB canvas as an image and sends it to an AI model
to produce a comprehensive design evaluation.

Supported modes:
  1. Text-based review (DeepSeek) — plan JSON + spec analysis
  2. Vision-based review — canvas screenshot + multimodal AI
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable

from openai import OpenAI

from .config import Settings


# ─────────────────────────────────────────────────────────────────
#  Veri Yapıları
# ─────────────────────────────────────────────────────────────────
@dataclass
class CategoryScore:
    name: str           # Kategori adı
    icon: str           # Emoji ikonu
    score: int          # 0-100
    detail: str = ""    # Detay açıklaması


@dataclass
class DesignIssue:
    severity: str       # "critical" | "warning" | "info"
    category: str       # Hangi kategoriye ait
    title: str
    description: str
    suggestion: str = ""


@dataclass
class DesignReview:
    overall_score: int = 0
    categories: list[CategoryScore] = field(default_factory=list)
    issues: list[DesignIssue] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    summary: str = ""
    timestamp: str = ""
    source: str = "text"  # "text" | "vision"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "overall_score": self.overall_score,
            "categories": [
                {"name": c.name, "icon": c.icon, "score": c.score, "detail": c.detail}
                for c in self.categories
            ],
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "title": i.title,
                    "description": i.description,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
            "recommendations": self.recommendations,
            "summary": self.summary,
            "source": self.source,
        }


# ─────────────────────────────────────────────────────────────────
#  Canvas → PNG yakalama
# ─────────────────────────────────────────────────────────────────
def capture_canvas_to_image(canvas, output_path: str | Path) -> Path:
    """
    Tkinter Canvas'ı PostScript → PNG olarak kaydeder.
    Ghostscript veya Pillow kullanarak dönüşüm yapar.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Canvas → PostScript
    ps_path = output_path.with_suffix(".ps")
    canvas.update_idletasks()
    canvas.postscript(file=str(ps_path), colormode="color")

    # PostScript → PNG dönüşümü
    try:
        # Pillow ile dönüşüm
        from PIL import Image

        img = Image.open(str(ps_path))
        img.save(str(output_path), "PNG")
        ps_path.unlink(missing_ok=True)
        return output_path
    except ImportError:
        pass

    # Ghostscript ile dönüşüm (fallback)
    try:
        subprocess.run(
            [
                "gs", "-dBATCH", "-dNOPAUSE", "-dSAFER",
                "-sDEVICE=png16m", "-r150",
                f"-sOutputFile={output_path}",
                str(ps_path),
            ],
            check=True,
            capture_output=True,
        )
        ps_path.unlink(missing_ok=True)
        return output_path
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # Hiçbiri yoksa PS dosyasını bırak
    if ps_path.exists():
        output_path = ps_path
    return output_path


def _image_to_base64(image_path: Path) -> str:
    """Resmi base64 string'e çevirir."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ─────────────────────────────────────────────────────────────────
#  Review geçmişi I/O
# ─────────────────────────────────────────────────────────────────
REVIEW_HISTORY_FILE = "review_history.json"


def load_review_history(project_dir: Path) -> list[dict]:
    path = project_dir / REVIEW_HISTORY_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_review_history(project_dir: Path, history: list[dict]) -> None:
    path = project_dir / REVIEW_HISTORY_FILE
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
#  Değerlendirme Kategorileri
# ─────────────────────────────────────────────────────────────────
REVIEW_CATEGORIES = [
    ("Electrical Correctness", "🔌"),
    ("Placement Quality", "📐"),
    ("EMC/EMI Compliance", "🛡️"),
    ("Manufacturability (DFM)", "🏭"),
    ("Design Standards", "📋"),
    ("Cost Optimization", "💰"),
]


# ─────────────────────────────────────────────────────────────────
#  AI Visual Reviewer sınıfı
# ─────────────────────────────────────────────────────────────────
class AIVisualReviewer:
    """Tasarımı metin ve/veya görsel analiz ile değerlendiren AI motoru."""

    def __init__(self, settings: Settings) -> None:
        self.client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.model = settings.deepseek_model

        # Vision API (opsiyonel)
        self._vision_client: OpenAI | None = None
        self._vision_model: str = ""
        vision_key = getattr(settings, "vision_api_key", "") or ""
        vision_url = getattr(settings, "vision_api_url", "") or ""
        if vision_key:
            self._vision_client = OpenAI(
                api_key=vision_key,
                base_url=vision_url or "https://api.openai.com/v1",
            )
            self._vision_model = getattr(settings, "vision_model", "") or "gpt-4o"

    @property
    def has_vision(self) -> bool:
        return self._vision_client is not None

    def review_design(
        self,
        plan_data: dict,
        project_spec: dict | None = None,
        image_path: Path | None = None,
        progress_cb: Callable[[str], None] | None = None,
    ) -> DesignReview:
        """Ana değerlendirme fonksiyonu — vision varsa görsel, yoksa text."""
        cb = progress_cb or (lambda _: None)

        review = DesignReview()
        review.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Vision API varsa ve resim varsa → görsel analiz
        if self.has_vision and image_path and image_path.exists():
            cb("🖼️  Performing visual analysis with Vision API...")
            try:
                review = self._vision_review(image_path, plan_data, project_spec)
                review.source = "vision"
                cb("✅ Visual analysis complete.")
                return review
            except Exception as exc:
                cb(f"⚠️  Vision hatası ({str(exc)[:80]}) — metin analiz'e geçiliyor...")

        # Fallback: text-based review
        cb("📝 Performing text-based AI analysis...")
        try:
            review = self._text_based_review(plan_data, project_spec)
            review.source = "text"
            cb("✅ Text analysis complete.")
        except Exception as exc:
            cb(f"⚠️  AI error ({str(exc)[:80]}) — switching to rule-based analysis...")
            review = self._rule_based_review(plan_data)
            review.source = "text"

        return review

    # ── Text-based review ────────────────────────────────────────
    def _text_based_review(
        self,
        plan_data: dict,
        project_spec: dict | None = None,
    ) -> DesignReview:
        """DeepSeek text API ile plan JSON analizi."""
        components_json = json.dumps(
            plan_data.get("components", []), indent=2, ensure_ascii=False
        )[:3000]
        nets_json = json.dumps(
            plan_data.get("nets", []), indent=2, ensure_ascii=False
        )[:2000]
        spec_text = json.dumps(project_spec, indent=2, ensure_ascii=False)[:1000] if project_spec else "Belirtilmemiş"
        checks = plan_data.get("design_checks", [])
        hints = plan_data.get("placement_hints", [])

        system_prompt = dedent("""
            You are a senior PCB design engineer. You will comprehensively 
            evaluate the given circuit design across 6 different categories.
            
            ONLY output JSON, do not write any other explanation.
        """).strip()

        user_prompt = dedent(f"""
            Analyze and evaluate the following PCB design.
            
            Project Specification:
            {spec_text}
            
            Components:
            {components_json}
            
            Nets:
            {nets_json}
            
            Design Checks:
            {json.dumps(checks, ensure_ascii=False)}
            
            Placement Hints:
            {json.dumps([h if isinstance(h, str) else h for h in hints], ensure_ascii=False, default=str)}
            
            Please perform the evaluation in this JSON format:
            {{
              "overall_score": <overall score between 0-100>,
              "categories": [
                {{
                  "name": "Electrical Correctness",
                  "icon": "🔌",
                  "score": <0-100>,
                  "detail": "Short description"
                }},
                {{
                  "name": "Placement Quality",
                  "icon": "📐",
                  "score": <0-100>,
                  "detail": "Short description"
                }},
                {{
                  "name": "EMC/EMI Compliance",
                  "icon": "🛡️",
                  "score": <0-100>,
                  "detail": "Short description"
                }},
                {{
                  "name": "Manufacturability (DFM)",
                  "icon": "🏭",
                  "score": <0-100>,
                  "detail": "Short description"
                }},
                {{
                  "name": "Design Standards",
                  "icon": "📋",
                  "score": <0-100>,
                  "detail": "Short description"
                }},
                {{
                  "name": "Cost Optimization",
                  "icon": "💰",
                  "score": <0-100>,
                  "detail": "Short description"
                }}
              ],
              "issues": [
                {{
                  "severity": "critical" | "warning" | "info",
                  "category": "Category name",
                  "title": "Issue title",
                  "description": "Detailed description",
                  "suggestion": "Solution suggestion"
                }}
              ],
              "recommendations": [
                "General recommendation 1",
                "General recommendation 2"
              ],
              "summary": "General summary and evaluation of the design (2-4 sentences)"
            }}
            
            Points to consider:
            - Check for existence of GND and VCC nets
            - Check for decoupling capacitors
            - Check for LED series resistors
            - Check for transistor base resistors
            - Check for open circuit connections
            - Evaluate component spacings
            - Comment on cost optimization
            - Respond in English
        """).strip()

        models_to_try = []
        for candidate in [self.model, "deepseek-chat", "deepseek-reasoner"]:
            if candidate and candidate not in models_to_try:
                models_to_try.append(candidate)

        response = None
        last_error = None
        for model_name in models_to_try:
            try:
                response = self.client.chat.completions.create(
                    model=model_name,
                    temperature=0.3,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                break
            except Exception as exc:
                last_error = exc
                if "model not exist" in str(exc).lower():
                    continue
                raise

        if response is None:
            if last_error:
                raise last_error
            raise RuntimeError("AI yanıtı alınamadı.")

        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        return self._parse_review_response(data)

    # ── Vision-based review ──────────────────────────────────────
    def _vision_review(
        self,
        image_path: Path,
        plan_data: dict,
        project_spec: dict | None = None,
    ) -> DesignReview:
        """Multimodal AI ile görsel + metin analizi."""
        if not self._vision_client:
            raise RuntimeError("Vision API yapılandırılmamış.")

        b64 = _image_to_base64(image_path)
        suffix = image_path.suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/jpeg"

        components_summary = ", ".join(
            f"{c.get('ref','?')}={c.get('value','?')}"
            for c in plan_data.get("components", [])[:20]
        )
        nets_summary = ", ".join(
            n.get("net_name", "?") for n in plan_data.get("nets", [])[:15]
        )

        prompt = dedent(f"""
            This is a screenshot of a PCB/schematic design.
            
            Components: {components_summary}
            Nets: {nets_summary}
            
            Evaluate this design in 6 categories and respond in the following JSON format:
            {{
              "overall_score": <0-100>,
              "categories": [
                {{"name": "Electrical Correctness", "icon": "🔌", "score": <0-100>, "detail": "..."}},
                {{"name": "Placement Quality", "icon": "📐", "score": <0-100>, "detail": "..."}},
                {{"name": "EMC/EMI Compliance", "icon": "🛡️", "score": <0-100>, "detail": "..."}},
                {{"name": "Manufacturability (DFM)", "icon": "🏭", "score": <0-100>, "detail": "..."}},
                {{"name": "Design Standards", "icon": "📋", "score": <0-100>, "detail": "..."}},
                {{"name": "Cost Optimization", "icon": "💰", "score": <0-100>, "detail": "..."}}
              ],
              "issues": [{{"severity": "critical"|"warning"|"info", "category": "...", "title": "...", "description": "...", "suggestion": "..."}}],
              "recommendations": ["..."],
              "summary": "General evaluation summary"
            }}
            Respond in English.
        """).strip()

        response = self._vision_client.chat.completions.create(
            model=self._vision_model,
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{b64}",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        content = response.choices[0].message.content or "{}"
        # JSON bloğu markdown code fence içinde olabilir
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0]

        data = json.loads(content.strip())
        return self._parse_review_response(data)

    # ── Rule-based fallback ──────────────────────────────────────
    def _rule_based_review(self, plan_data: dict) -> DesignReview:
        """API kullanılamadığında kural tabanlı basit değerlendirme."""
        review = DesignReview()
        review.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        components = plan_data.get("components", [])
        nets = plan_data.get("nets", [])
        net_names = {n.get("net_name", "").lower() for n in nets}

        scores = {}
        issues: list[DesignIssue] = []
        recs: list[str] = []

        # ── Electrical Correctness ─────────────────────────────────
        elec_score = 80
        if "gnd" not in net_names:
            elec_score -= 25
            issues.append(DesignIssue("critical", "Electrical Correctness",
                                      "GND net missing",
                                      "No GND (ground) connection found in the circuit.",
                                      "Connect all component GND pins to a common GND net."))
        if not any(v in net_names for v in ("vcc", "vdd", "vin", "+5v", "+3v3", "3v3", "5v")):
            elec_score -= 20
            issues.append(DesignIssue("critical", "Electrical Correctness",
                                      "Power net missing",
                                      "VCC/VDD power distribution net not found.",
                                      "Explicitly define the power supply net."))
        # Floating pin check
        all_connected = set()
        for n in nets:
            for node in n.get("nodes", []):
                all_connected.add(node.split(".")[0] if "." in node else node)
        unconnected = [c.get("ref", "?") for c in components if c.get("ref", "?") not in all_connected]
        if unconnected:
            elec_score -= 10 * min(len(unconnected), 3)
            issues.append(DesignIssue("warning", "Electrical Correctness",
                                      f"{len(unconnected)} unconnected components",
                                      f"The following components are not connected to any net: {', '.join(unconnected[:5])}",
                                      "Ensure every component is connected to at least one net."))
        scores["Electrical Correctness"] = max(0, elec_score)

        # ── Placement Quality ────────────────────────────────────
        layout_score = 70
        if len(components) > 10:
            layout_score += 5  # Complexity bonus
        scores["Placement Quality"] = layout_score

        # ── EMC/EMI ──────────────────────────────────────────────
        emc_score = 65
        caps = [c for c in components if c.get("ref", "").startswith("C")]
        ics = [c for c in components if c.get("ref", "").startswith("U")]
        if ics and not caps:
            emc_score -= 20
            issues.append(DesignIssue("warning", "EMC/EMI Compliance",
                                      "Decoupling capacitor missing",
                                      "No decoupling capacitors found for ICs.",
                                      "Add 100nF capacitors to VCC-GND pins of each IC."))
            recs.append("Add 100nF decoupling capacitors next to each IC.")
        scores["EMC/EMI Compliance"] = max(0, emc_score)

        # ── DFM ──────────────────────────────────────────────────
        dfm_score = 75
        for c in components:
            if not c.get("footprint"):
                dfm_score -= 5
        if dfm_score < 50:
            issues.append(DesignIssue("info", "Manufacturability (DFM)",
                                      "Components missing footprints",
                                      "Some components do not have footprint information defined.",
                                      "Assign appropriate footprints to all components."))
        scores["Manufacturability (DFM)"] = max(0, dfm_score)

        # ── Design Standards ─────────────────────────────────
        std_score = 80
        refs = [c.get("ref", "") for c in components]
        if len(refs) != len(set(refs)):
            std_score -= 20
            issues.append(DesignIssue("critical", "Design Standards",
                                      "Duplicate reference designators",
                                      "Multiple components are using the same reference designator.",
                                      "Assign unique reference designators to each component."))
        scores["Design Standards"] = max(0, std_score)

        # ── Cost ──────────────────────────────────────────────
        cost_score = 80
        if len(components) > 20:
            cost_score -= 10
            recs.append("You can reduce costs by reducing the component count.")
        scores["Cost Optimization"] = max(0, cost_score)

        # ── Sonuçları derle ──────────────────────────────────────
        cat_icons = dict(REVIEW_CATEGORIES)
        review.categories = [
            CategoryScore(
                name=name,
                icon=cat_icons.get(name, "📊"),
                score=scores.get(name, 70),
                detail="Kural tabanlı analiz sonucu.",
            )
            for name, _ in REVIEW_CATEGORIES
        ]
        review.issues = issues
        review.recommendations = recs or ["General design checks should be performed."]
        review.overall_score = sum(c.score for c in review.categories) // len(review.categories) if review.categories else 0
        review.summary = (
            f"Design contains {len(components)} components and {len(nets)} nets. "
            f"Overall score: {review.overall_score}/100. "
            f"{len(issues)} issues detected."
        )
        return review

    # ── Response parsing ─────────────────────────────────────────
    def _parse_review_response(self, data: dict) -> DesignReview:
        """AI JSON yanıtını DesignReview nesnesine çevirir."""
        review = DesignReview()
        review.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        review.overall_score = int(data.get("overall_score", 0))

        for cat_data in data.get("categories", []):
            review.categories.append(
                CategoryScore(
                    name=cat_data.get("name", "?"),
                    icon=cat_data.get("icon", "📊"),
                    score=int(cat_data.get("score", 0)),
                    detail=cat_data.get("detail", ""),
                )
            )

        for issue_data in data.get("issues", []):
            review.issues.append(
                DesignIssue(
                    severity=issue_data.get("severity", "info"),
                    category=issue_data.get("category", "Genel"),
                    title=issue_data.get("title", ""),
                    description=issue_data.get("description", ""),
                    suggestion=issue_data.get("suggestion", ""),
                )
            )

        review.recommendations = data.get("recommendations", [])
        review.summary = data.get("summary", "")
        return review
