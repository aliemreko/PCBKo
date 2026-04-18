"""
PCB Circuit Simulator
====================
Reads the generated design_plan.json file and performs rule-based
electrical tests and simple analytical simulations on the circuit.
It is not based on actual SPICE; it includes goal-oriented heuristic
tests and provides a "PASS / FAIL" decision.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Sabitler ────────────────────────────────────────────────────
LED_VF       = 2.0    # V  – tipik kırmızı LED ileri gerilimi
LED_IF_MIN   = 5e-3   # A  – 5 mA
LED_IF_MAX   = 30e-3  # A  – 30 mA
IMAX_TRACE   = 1.0    # A  – 0.25 mm trace için güvenli limit
VCC_NAMES    = {"vcc", "vdd", "vin", "+5v", "5v", "+3v3", "3v3", "+12v", "power", "v+"}
GND_NAMES    = {"gnd", "gnd1", "gnd2", "agnd", "dgnd", "0v", "ground"}


# ─────────────────────────────────────────────────────────────────
#  Veri yapıları
# ─────────────────────────────────────────────────────────────────
@dataclass
class TestResult:
    name: str            # Test adı
    status: str          # "PASS" | "FAIL" | "WARN" | "INFO"
    detail: str          # Açıklama
    value: Any = None    # İsteğe bağlı hesaplanan değer


@dataclass
class SimulationReport:
    tests: list[TestResult] = field(default_factory=list)
    overall: str = "UNKNOWN"   # "PASS" | "FAIL" | "WARN"
    summary: str = ""

    def add(self, result: TestResult) -> None:
        self.tests.append(result)

    def finalize(self) -> None:
        if any(t.status == "FAIL" for t in self.tests):
            self.overall = "FAIL"
        elif any(t.status == "WARN" for t in self.tests):
            self.overall = "WARN"
        else:
            self.overall = "PASS"
        pass_c  = sum(1 for t in self.tests if t.status == "PASS")
        fail_c  = sum(1 for t in self.tests if t.status == "FAIL")
        warn_c  = sum(1 for t in self.tests if t.status == "WARN")
        self.summary = (
            f"{len(self.tests)} tests — ✅ {pass_c} passed  "
            f"❌ {fail_c} failed  ⚠️ {warn_c} warnings"
        )


# ─────────────────────────────────────────────────────────────────
#  Yardımcı: değer ayrıştıcı  (1k→1000, 10uF→10e-6, 220→220 …)
# ─────────────────────────────────────────────────────────────────
_UNIT_MAP = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
    "f": 1e-15,
}

def _parse_value(s: str) -> float | None:
    """'10uF' → 10e-6, '220' → 220.0, '1k' → 1000.0"""
    s = s.strip().lower().replace(",", ".")
    # remove unit suffix (r, f, h, ohm, v, a …)
    s = re.sub(r"[a-z]+$", lambda m: _mul(m.group()), s, count=1)
    try:
        return float(s)
    except ValueError:
        return None

def _mul(unit: str) -> str:
    for key, val in _UNIT_MAP.items():
        if unit.startswith(key):
            return f"e{int(round(__import__('math').log10(val)))}"
    return ""

def parse_value(raw: str) -> float | None:
    raw = raw.strip().lower()
    # "4.7k", "10uf", "100nf", "220"
    m = re.match(r"^([\d.]+)\s*([a-z]*)", raw)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2).lower().replace("ohm", "").replace("ω","")
    mult = _UNIT_MAP.get(suffix, _UNIT_MAP.get(suffix[:1], 1.0))
    return num * mult


# ─────────────────────────────────────────────────────────────────
#  Ana simülatör sınıfı
# ─────────────────────────────────────────────────────────────────
class CircuitSimulator:
    def __init__(self, design_plan: dict) -> None:
        self.plan       = design_plan
        self.components = design_plan.get("components", [])
        self.nets       = design_plan.get("nets", [])
        self.report     = SimulationReport()

    # ── public ──────────────────────────────────────────────────
    def run_all(self) -> SimulationReport:
        self._test_power_rails()
        self._test_short_circuit()
        self._test_open_circuit()
        self._test_led_current()
        self._test_decoupling_caps()
        self._test_transistor_base_resistor()
        self._test_component_count()
        self._test_opamp_supply()
        self._test_net_connectivity()
        self._test_trace_current()
        self.report.finalize()
        return self.report

    # ── Yardımcılar ─────────────────────────────────────────────
    def _refs_by_type(self, prefix: str) -> list[dict]:
        return [c for c in self.components if c.get("ref", "").startswith(prefix)]

    def _net_names(self) -> set[str]:
        names: set[str] = set()
        for n in self.nets:
            # support both 'name' (legacy) and 'net_name' (current schema)
            nn = n.get("net_name") or n.get("name", "")
            if nn:
                names.add(nn.lower())
        return names

    def _pins_in_net(self, net_name_lower: str) -> list[dict]:
        for n in self.nets:
            nn = (n.get("net_name") or n.get("name", "")).lower()
            if nn == net_name_lower:
                # 'nodes' is list[str] like "U1.3"; 'pins' is list[dict] (legacy)
                raw = n.get("nodes") or n.get("pins", [])
                if raw and isinstance(raw[0], str):
                    # convert "REF.PIN" strings to {ref, pin} dicts
                    result = []
                    for node in raw:
                        parts = node.split(".")
                        result.append({"ref": parts[0], "pin": parts[1] if len(parts) > 1 else "1"})
                    return result
                return raw
        return []

    def _find_power_net(self) -> str | None:
        for nn in self._net_names():
            if nn in VCC_NAMES or any(nn.startswith(v) for v in VCC_NAMES):
                return nn
        return None

    def _find_gnd_net(self) -> str | None:
        for nn in self._net_names():
            if nn in GND_NAMES:
                return nn
        return None

    def _supply_voltage(self) -> float:
        """Design title veya bileşen değerinden besleme voltajını tahmin eder."""
        title = self.plan.get("title", "").lower()
        m = re.search(r"(\d+(?:\.\d+)?)\s*v", title)
        if m:
            return float(m.group(1))
        return 5.0   # varsayılan

    # ── Testler ─────────────────────────────────────────────────

    def _test_power_rails(self) -> None:
        vcc = self._find_power_net()
        gnd = self._find_gnd_net()
        if vcc and gnd:
            self.report.add(TestResult(
                "Power Rails",
                "PASS",
                f"VCC net: '{vcc.upper()}'  GND net: '{gnd.upper()}' found.",
            ))
        else:
            missing = []
            if not vcc: missing.append("VCC/VDD/VIN")
            if not gnd: missing.append("GND")
            self.report.add(TestResult(
                "Power Rails",
                "FAIL" if len(missing) == 2 else "WARN",
                f"Missing net(s): {', '.join(missing)}.  "
                "Netlist might be incomplete.",
            ))

    def _test_short_circuit(self) -> None:
        vcc = self._find_power_net()
        gnd = self._find_gnd_net()
        if not vcc or not gnd:
            self.report.add(TestResult("Short Circuit", "INFO", "Power rails not found; test skipped."))
            return
        vcc_pins = {(p.get("ref"), p.get("pin")) for p in self._pins_in_net(vcc)}
        gnd_pins = {(p.get("ref"), p.get("pin")) for p in self._pins_in_net(gnd)}
        common = vcc_pins & gnd_pins
        if common:
            self.report.add(TestResult(
                "Short Circuit", "FAIL",
                f"VCC and GND meet at the same pin: {common}",
            ))
        else:
            self.report.add(TestResult("Short Circuit", "PASS", "No short circuit between VCC and GND."))

    def _test_open_circuit(self) -> None:
        connected_refs: set[str] = set()
        for n in self.nets:
            nodes = n.get("nodes") or n.get("pins", [])
            for p in nodes:
                if isinstance(p, str):
                    connected_refs.add(p.split(".")[0])
                else:
                    connected_refs.add(p.get("ref", ""))
        all_refs = {c.get("ref", "") for c in self.components}
        floating = all_refs - connected_refs - {""}
        if floating:
            self.report.add(TestResult(
                "Open Circuit (Floating)",
                "WARN",
                f"The following components appear to be unconnected to any net: {', '.join(sorted(floating))}. "
                "Netlist might be incomplete.",
            ))
        else:
            self.report.add(TestResult(
                "Open Circuit (Floating)", "PASS",
                "All components are connected to at least one net.",
            ))

    def _test_led_current(self) -> None:
        leds = self._refs_by_type("D")
        resistors = self._refs_by_type("R")
        if not leds:
            self.report.add(TestResult("LED Current Analysis", "INFO", "No LED (D*) components in circuit."))
            return

        vcc_v = self._supply_voltage()
        details: list[str] = []
        all_ok = True

        for led in leds:
            # LED ile seri bir direnç ara
            led_ref = led.get("ref", "")
            # Net listesinde LED'in paylaştığı dirençleri bul
            series_r: list[dict] = []
            for n in self.nets:
                nodes = n.get("nodes") or n.get("pins", [])
                refs_in_net: set[str] = set()
                for p in nodes:
                    refs_in_net.add(p.split(".")[0] if isinstance(p, str) else p.get("ref", ""))
                if led_ref in refs_in_net:
                    for r in resistors:
                        if r.get("ref") in refs_in_net:
                            series_r.append(r)

            if not series_r:
                details.append(f"⚠️  {led_ref}: no series resistor found — LED will burn or be damaged!")
                all_ok = False
                continue

            # en küçük dirençle akım hesapla
            r_vals = [parse_value(r.get("value", "1k")) for r in series_r]
            r_vals = [v for v in r_vals if v and v > 0]
            if r_vals:
                r_min = min(r_vals)
                i_led = (vcc_v - LED_VF) / r_min
                if i_led < LED_IF_MIN:
                    details.append(
                        f"⚠️  {led_ref}: I_LED={i_led*1000:.1f} mA — too low, LED might be dim. "
                        f"(R={r_min:.0f} Ω)"
                    )
                    all_ok = False
                elif i_led > LED_IF_MAX:
                    details.append(
                        f"❌  {led_ref}: I_LED={i_led*1000:.1f} mA — too high, LED might be damaged! "
                        f"(R={r_min:.0f} Ω)"
                    )
                    all_ok = False
                else:
                    details.append(
                        f"✅  {led_ref}: I_LED={i_led*1000:.1f} mA — OK (R={r_min:.0f} Ω)"
                    )
 
        status = "PASS" if all_ok else ("WARN" if all("⚠️" in d for d in details) else "FAIL")
        self.report.add(TestResult(
            "LED Current Analysis", status,
            "\n".join(details) if details else "LED test passed.",
            value=details,
        ))

    def _test_decoupling_caps(self) -> None:
        ics = self._refs_by_type("U")
        caps = self._refs_by_type("C")
        if not ics:
            self.report.add(TestResult("Decoupling Capacitors", "INFO", "No IC (U*) components."))
            return

        # 100 nF – 10 uF arasındaki caps "decoupling" sayılır
        decoupling = []
        for c in caps:
            v = parse_value(c.get("value", ""))
            if v is not None and 1e-10 <= v <= 100e-6:
                decoupling.append(c)

        if not decoupling:
            self.report.add(TestResult(
                "Decoupling Capacitors", "WARN",
                f"{len(ics)} ICs found but no decoupling capacitor (100nF–10µF) detected. "
                "Power noise issues may occur.",
            ))
        elif len(decoupling) < len(ics):
            self.report.add(TestResult(
                "Decoupling Capacitors", "WARN",
                f"{len(ics)} ICs found with {len(decoupling)} decoupling caps. "
                "At least 1 per IC is recommended.",
            ))
        else:
            self.report.add(TestResult(
                "Decoupling Capacitors", "PASS",
                f"{len(decoupling)} decoupling capacitors present — sufficient.",
            ))

    def _test_transistor_base_resistor(self) -> None:
        transistors = self._refs_by_type("Q")
        resistors   = self._refs_by_type("R")
        if not transistors:
            self.report.add(TestResult("Transistor Base Resistor", "INFO", "No transistor (Q*) components."))
            return

        details: list[str] = []
        all_ok = True
        for q in transistors:
            q_ref = q.get("ref", "")
            # Base pinin bağlı olduğu nette direnç var mı?
            base_net_has_r = False
            for n in self.nets:
                nodes = n.get("nodes") or n.get("pins", [])
                # build (ref, pin) pairs
                pairs: list[tuple[str,str]] = []
                for p in nodes:
                    if isinstance(p, str):
                        parts = p.split(".")
                        pairs.append((parts[0], parts[1] if len(parts) > 1 else "1"))
                    else:
                        pairs.append((p.get("ref",""), str(p.get("pin","1"))))
                if any(ref == q_ref and pin.upper() in ("B","BASE","2","1") for ref, pin in pairs):
                    refs = {ref for ref, _ in pairs}
                    if any(r.get("ref") in refs for r in resistors):
                        base_net_has_r = True
                        break

            if base_net_has_r:
                details.append(f"✅  {q_ref}: base direnci mevcut.")
            else:
                details.append(f"⚠️  {q_ref}: base direnci bulunamadı — base akımı kontrolsüz olabilir.")
                all_ok = False

        self.report.add(TestResult(
            "Transistör Base Direnci",
            "PASS" if all_ok else "WARN",
            "\n".join(details),
        ))

    def _test_component_count(self) -> None:
        n = len(self.components)
        if n == 0:
            self.report.add(TestResult("Component Count", "FAIL", "No components found!"))
        elif n < 3:
            self.report.add(TestResult("Component Count", "WARN", f"Only {n} components — circuit is too simple."))
        else:
            self.report.add(TestResult("Component Count", "PASS", f"Total of {n} components present."))

    def _test_opamp_supply(self) -> None:
        opamps = self._refs_by_type("U")
        if not opamps:
            return
        vcc = self._find_power_net()
        gnd = self._find_gnd_net()
        if vcc and gnd:
            self.report.add(TestResult(
                "Op-Amp Supply",
                "PASS",
                f"VCC and GND rails present for {len(opamps)} IC/Op-Amp components.",
            ))
        else:
            self.report.add(TestResult(
                "Op-Amp Supply", "WARN",
                f"{len(opamps)} IC/Op-Amp present but missing power rail: "
                f"{'VCC' if not vcc else ''} {'GND' if not gnd else ''} not found.",
            ))

    def _test_net_connectivity(self) -> None:
        total_nets = len(self.nets)
        single_pin = []
        for n in self.nets:
            nodes = n.get("nodes") or n.get("pins", [])
            if len(nodes) <= 1:
                single_pin.append(n)

        if single_pin:
            names = [(n.get("net_name") or n.get("name") or "?") for n in single_pin[:5]]
            self.report.add(TestResult(
                "Net Connectivity",
                "WARN",
                f"{len(single_pin)} nets contain only 1 node (open end?): {names}",
            ))
        else:
            self.report.add(TestResult(
                "Net Connectivity", "PASS",
                f"Total of {total_nets} nets, all contain 2+ nodes.",
            ))

    def _test_trace_current(self) -> None:
        """Güç tracelarındaki tahmini akımı kontrol eder."""
        leds  = self._refs_by_type("D")
        led_count = len(leds)
        est_current = led_count * 0.020  # 20 mA per LED

        transistors = self._refs_by_type("Q")
        if transistors:
            est_current += 0.050  # switching loss estimate

        if est_current == 0:
            return

        trace_w = 0.25  # mm default
        # rough: 0.25mm trace ~ 0.5 A limit (1oz copper, 20°C rise)
        max_a = trace_w / 0.25 * 0.5
        if est_current > max_a:
            self.report.add(TestResult(
                "Trace Current Capacity",
                "WARN",
                f"Estimated current ~{est_current*1000:.0f} mA, "
                f"0.25 mm trace limit ~{max_a*1000:.0f} mA. "
                "Consider widening power traces.",
            ))
        else:
            self.report.add(TestResult(
                "Trace Current Capacity",
                "PASS",
                f"Estimated current ~{est_current*1000:.0f} mA — sufficient for 0.25 mm trace.",
            ))


# ─────────────────────────────────────────────────────────────────
#  Dosya yükleyicisi
# ─────────────────────────────────────────────────────────────────
def load_design_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def simulate_project(project_dir: Path) -> SimulationReport:
    plan_path = project_dir / "design_plan.json"
    if not plan_path.exists():
        r = SimulationReport()
        r.add(TestResult("File Loading", "FAIL",
                         f"design_plan.json not found: {plan_path}"))
        r.finalize()
        return r
    plan = load_design_plan(plan_path)
    sim = CircuitSimulator(plan)
    return sim.run_all()
