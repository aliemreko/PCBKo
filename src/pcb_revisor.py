"""
PCB Revision Engine
===================
Receives simulation test results → Analyzes with DeepSeek →
Generates a corrected DesignPlan → Rebuilds the project.
Revision history is saved as revision_log.json in the project directory.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Any

from openai import OpenAI

from .circuit_simulator import SimulationReport, simulate_project
from .component_autofill import autofill_components
from .config import Settings
from .kicad_bootstrap import write_kicad_project_files
from .kicad_generator import write_kicad_compatible_outputs
from .layout_outputs import write_layout_outputs
from .models import DesignPlan, ProjectSpec
from .plan_normalizer import normalize_plan


# ─────────────────────────────────────────────────────────────────
#  Data Structures
# ─────────────────────────────────────────────────────────────────
class RevisionResult:
    def __init__(self) -> None:
        self.revision_id: str       = ""
        self.timestamp: str         = ""
        self.issues_found: list[str] = []
        self.suggestions: list[str] = []
        self.changes_applied: list[str] = []
        self.new_plan: DesignPlan | None = None
        self.files: dict[str, str]  = {}
        self.success: bool          = False
        self.error: str             = ""
        self.sim_before: SimulationReport | None = None
        self.sim_after:  SimulationReport | None = None

    def to_dict(self) -> dict:
        return {
            "revision_id": self.revision_id,
            "timestamp": self.timestamp,
            "issues_found": self.issues_found,
            "suggestions": self.suggestions,
            "changes_applied": self.changes_applied,
            "success": self.success,
            "error": self.error,
            "sim_before_overall": self.sim_before.overall if self.sim_before else "?",
            "sim_before_summary": self.sim_before.summary if self.sim_before else "",
            "sim_after_overall":  self.sim_after.overall  if self.sim_after  else "?",
            "sim_after_summary":  self.sim_after.summary  if self.sim_after  else "",
        }


# ─────────────────────────────────────────────────────────────────
#  Revision Log I/O
# ─────────────────────────────────────────────────────────────────
REVISION_LOG_FILE = "revision_log.json"

def load_revision_log(project_dir: Path) -> list[dict]:
    log_path = project_dir / REVISION_LOG_FILE
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def save_revision_log(project_dir: Path, log: list[dict]) -> None:
    log_path = project_dir / REVISION_LOG_FILE
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
#  Main Revisor Class
# ─────────────────────────────────────────────────────────────────
class PCBRevisor:
    def __init__(self, settings: Settings) -> None:
        self.client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.model = settings.deepseek_model

    # ── public: get plan suggestions (suggestions only first) ────
    def get_suggestions(
        self,
        project_dir: Path,
        sim_report: SimulationReport | None = None,
    ) -> list[str]:
        """Get improvement suggestions from AI based on simulation report."""
        if sim_report is None:
            sim_report = simulate_project(project_dir)

        plan_path = project_dir / "design_plan.json"
        if not plan_path.exists():
            return ["design_plan.json not found."]

        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        issues_text = self._format_issues(sim_report)

        prompt = dedent(f"""
            Analyze the current PCB design and generate ONLY a list of suggestions.
            
            Current components:
            {json.dumps([c for c in plan_data.get('components', [])], indent=2, ensure_ascii=False)[:2000]}
            
            Simulation results:
            {issues_text}
            
            Please return ONLY this structure in JSON format:
            {{
              "suggestions": [
                "Suggestion 1: explanation",
                "Suggestion 2: explanation"
              ]
            }}
        """).strip()

        response = self.client.chat.completions.create(
            model=self._best_model(),
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a PCB design expert. Give suggestions in English."},
                {"role": "user", "content": prompt},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        return data.get("suggestions", [])

    # ── public: apply revision ───────────────────────────────────────
    def apply_revision(
        self,
        project_dir: Path,
        spec: ProjectSpec,
        selected_suggestions: list[str] | None = None,
        progress_cb=None,          # callable(msg: str)
    ) -> RevisionResult:
        result = RevisionResult()
        result.timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log                = load_revision_log(project_dir)
        result.revision_id = f"Rev{len(log) + 1:03d}"

        def _progress(msg: str):
            if progress_cb:
                progress_cb(msg)

        try:
            # 1. Simulate current state
            _progress("🔍 Simulating current design...")
            result.sim_before = simulate_project(project_dir)
            result.issues_found = [
                f"[{t.status}] {t.name}: {t.detail}"
                for t in result.sim_before.tests
                if t.status in ("FAIL", "WARN")
            ]

            if not result.issues_found and result.sim_before.overall == "PASS":
                result.success = True
                result.suggestions = ["The current design already passes all tests — no revision needed."]
                return result

            # 2. Load current plan
            plan_path = project_dir / "design_plan.json"
            if not plan_path.exists():
                result.error = "design_plan.json not found."
                return result

            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))

            # 3. Get revised plan from AI
            _progress("🤖 Connecting to DeepSeek API — generating revised plan...")
            try:
                revised_plan_data = self._ask_ai_for_revision(
                    plan_data, result.issues_found, selected_suggestions or []
                )
                result.suggestions = revised_plan_data.pop("_suggestions", [])
                result.changes_applied = revised_plan_data.pop("_changes", [])
            except Exception as ai_exc:
                _progress(f"⚠️  AI could not respond ({ai_exc!s:.80}) — applying rule-based fixes...")
                revised_plan_data, result.changes_applied = apply_rule_based_fixes(
                    plan_data, result.issues_found
                )
                result.suggestions = result.changes_applied

            # 4. Backup old design
            _progress(f"💾 Backing up current design ({result.revision_id})...")
            backup_dir = project_dir / "revisions" / result.revision_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            for f in project_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, backup_dir / f.name)

            # 5. Save new plan
            new_plan_path = project_dir / "design_plan.json"
            new_plan_path.write_text(
                json.dumps(revised_plan_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # 6. Re-run pipeline
            _progress("⚙️  Regenerating KiCad files...")
            plan_obj = DesignPlan.model_validate(revised_plan_data)
            plan_obj = normalize_plan(autofill_components(plan_obj))
            result.new_plan = plan_obj

            files = write_kicad_compatible_outputs(plan_obj, project_dir)
            _progress("📐 Calculating placement & routing...")
            layout_files = write_layout_outputs(plan_obj, spec, project_dir)
            files.update(layout_files)
            _progress("📁 Writing KiCad project files...")
            kicad_files = write_kicad_project_files(spec, project_dir)
            files.update(kicad_files)
            result.files = {k: str(v) for k, v in files.items()}

            # 7. Simulate post-revision
            _progress("🔬 Testing revised design...")
            result.sim_after = simulate_project(project_dir)

            result.success = True
            _progress(
                f"✅ Revision complete! "
                f"{result.sim_before.overall} → {result.sim_after.overall}"
            )

        except Exception as exc:
            import traceback
            result.error = traceback.format_exc()
            result.success = False

        finally:
            # Add to history
            log.append(result.to_dict())
            save_revision_log(project_dir, log)

        return result

    # ── private ──────────────────────────────────────────────────────
    def _best_model(self) -> str:
        return self.model or "deepseek-chat"

    def _format_issues(self, report: SimulationReport) -> str:
        lines = [f"Overall Result: {report.overall}", report.summary, ""]
        for t in report.tests:
            if t.status in ("FAIL", "WARN"):
                lines.append(f"  [{t.status}] {t.name}: {t.detail}")
        return "\n".join(lines)

    def _ask_ai_for_revision(
        self,
        current_plan: dict,
        issues: list[str],
        extra_suggestions: list[str],
    ) -> dict:
        issues_text    = "\n".join(f"- {i}" for i in issues)
        extra_text     = "\n".join(f"- {s}" for s in extra_suggestions) if extra_suggestions else "None"
        components_json = json.dumps(current_plan.get("components", []), indent=2, ensure_ascii=False)
        nets_json       = json.dumps(current_plan.get("nets", []),       indent=2, ensure_ascii=False)

        system_prompt = dedent("""
            You are an expert PCB design engineer.
            You will revise an existing circuit design based on simulation findings.
            ONLY output JSON. Do not add comments or explanations.
        """).strip()

        user_prompt = dedent(f"""
            Current design title: {current_plan.get('title', '?')}

            Current components:
            {components_json[:3000]}

            Current nets:
            {nets_json[:2000]}

            Simulation issues:
            {issues_text}

            User's extra suggestions:
            {extra_text}

            Task:
            Produce a revised design plan addressing the issues.
            Use this JSON schema:
            {{
              "title": string,
              "assumptions": string[],
              "components": [{{"ref": str, "value": str, "notes": str}}],
              "nets": [{{"net_name": str, "nodes": ["REF.PIN"]}}],
              "placement_hints": [{{"target": str, "hint": str}}],
              "design_checks": string[],
              "_suggestions": ["What changed: explanation"],
              "_changes": ["Change 1", "Change 2"]
            }}
            
            Important rules:
            - Always include GND and VCC nets
            - Add at least 1 decoupling capacitor (100nF) for each IC
            - Calculate appropriate series resistor values for LEDs (e.g., 220Ω for 5V)
            - Connect a resistor to transistor base pins (e.g., 1kΩ)
            - Fix all FAIL and WARN conditions from simulation issues
            - Respond in English
        """).strip()

        models = [self._best_model(), "deepseek-chat", "deepseek-reasoner"]
        seen: list[str] = []
        for m in models:
            if m and m not in seen:
                seen.append(m)

        last_exc: Exception | None = None
        for model_name in seen:
            try:
                response = self.client.chat.completions.create(
                    model=model_name,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                )
                content = response.choices[0].message.content or "{}"
                return json.loads(content)
            except Exception as exc:
                last_exc = exc
                if "model not exist" in str(exc).lower():
                    continue
                raise

        raise RuntimeError(f"Could not get DeepSeek revision response: {last_exc}")


# ─────────────────────────────────────────────────────────────────
#  Rule-based offline fallback
# ─────────────────────────────────────────────────────────────────
def apply_rule_based_fixes(plan_data: dict, issues: list[str]) -> tuple[dict, list[str]]:
    """
    Applies rule-based automatic fixes when AI is unavailable.
    Returns: (corrected_plan, changes_applied)
    """
    import copy
    plan = copy.deepcopy(plan_data)
    changes: list[str] = []
    components: list[dict] = plan.setdefault("components", [])
    nets: list[dict] = plan.setdefault("nets", [])

    # —— Rule 1: Add GND net if missing ——
    net_names_lower = {n.get("net_name", "").lower() for n in nets}
    if "gnd" not in net_names_lower:
        gnd_nodes = [f"{c['ref']}.{_gnd_pin(c)}" for c in components if _has_gnd_pin(c)]
        if gnd_nodes:
            nets.append({"net_name": "GND", "nodes": gnd_nodes})
            changes.append("GND net added.")

    # —— Rule 2: Add VCC net if missing ——
    if not any(n in net_names_lower for n in ("vcc", "vdd", "vin", "+5v")):
        vcc_nodes = [f"{c['ref']}.{_vcc_pin(c)}" for c in components if _has_vcc_pin(c)]
        if vcc_nodes:
            nets.append({"net_name": "VCC", "nodes": vcc_nodes})
            changes.append("VCC net added.")

    # —— Rule 3: Add 220Ω R for LED if missing series resistor ——
    leds = [c for c in components if c.get("ref", "").startswith("D")]
    resistors = [c for c in components if c.get("ref", "").startswith("R")]
    if leds and not resistors:
        r_ref = "R1"
        components.append({"ref": r_ref, "value": "220", "notes": "LED series resistor (added by rule)"})
        changes.append(f"{r_ref} (220Ω) added as LED series resistor.")

    # —— Rule 4: Add decoupling cap if IC exists ——
    ics  = [c for c in components if c.get("ref", "").startswith("U")]
    caps = [c for c in components if c.get("ref", "").startswith("C")]
    if ics and not caps:
        c_ref = "C1"
        components.append({"ref": c_ref, "value": "100nF", "notes": "Decoupling capacitor (added by rule)"})
        changes.append(f"{c_ref} (100nF) added as decoupling capacitor.")

    return plan, changes


def _gnd_pin(comp: dict) -> str:
    val = comp.get("value", "").lower()
    if "lm358" in val: return "4"
    return "2"

def _vcc_pin(comp: dict) -> str:
    val = comp.get("value", "").lower()
    if "lm358" in val: return "8"
    return "1"

def _has_gnd_pin(comp: dict) -> bool:
    return comp.get("ref", "")[0] in ("U", "Q", "D", "J")

def _has_vcc_pin(comp: dict) -> bool:
    return comp.get("ref", "")[0] in ("U", "J")
