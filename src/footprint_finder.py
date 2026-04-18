"""
KiCad Library Auto-Discovery
Scans installed KiCad symbol & footprint libraries to automatically find
the best match for any component value string.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import pcbnew  # type: ignore
except Exception:
    pcbnew = None


# ── Default library paths ────────────────────────────────────────
_FP_ROOT = Path("/usr/share/kicad/footprints")
_SYM_ROOT = Path("/usr/share/kicad/symbols")


@dataclass
class LibraryMatch:
    """A matched library entry with a relevance score."""
    library: str       # e.g. "RF_Module"
    name: str          # e.g. "ESP32-C3-WROOM-02"
    score: float       # higher = better match
    full_id: str = ""  # e.g. "RF_Module:ESP32-C3-WROOM-02"

    def __post_init__(self) -> None:
        if not self.full_id:
            self.full_id = f"{self.library}:{self.name}"


# ── Singleton index ──────────────────────────────────────────────
@dataclass
class _LibraryIndex:
    footprints: list[tuple[str, str]] = field(default_factory=list)   # (lib, name)
    symbols: list[tuple[str, str]] = field(default_factory=list)
    sym_to_fp: dict[str, str] = field(default_factory=dict)  # "Lib:Symbol" -> "Lib:Footprint"
    _built: bool = False

    def build(self) -> None:
        if self._built:
            return

        _sym_re = re.compile(r"^\s{2}\(symbol\s+\"([^\"]+)\"")
        _fp_re = re.compile(r'property\s+"Footprint"\s+"([^"]+)"')

        # Footprints: scan *.pretty directories
        if _FP_ROOT.is_dir():
            for lib_dir in sorted(_FP_ROOT.glob("*.pretty")):
                lib = lib_dir.stem
                for mod_file in lib_dir.glob("*.kicad_mod"):
                    self.footprints.append((lib, mod_file.stem))

        # Symbols: parse .kicad_sym files for top-level symbol names
        # Also extract default footprint mapping from each symbol
        if _SYM_ROOT.is_dir():
            for sym_file in sorted(_SYM_ROOT.glob("*.kicad_sym")):
                lib = sym_file.stem
                current_sym: str | None = None
                try:
                    for line in sym_file.open(errors="ignore"):
                        m = _sym_re.match(line)
                        if m:
                            name = m.group(1)
                            if name.endswith(("_0_0", "_0_1", "_1_0", "_1_1")):
                                current_sym = None
                            else:
                                current_sym = name
                                self.symbols.append((lib, name))
                            continue
                        if current_sym:
                            fm = _fp_re.search(line)
                            if fm:
                                full_id = f"{lib}:{current_sym}"
                                self.sym_to_fp[full_id] = fm.group(1)
                                current_sym = None
                except OSError:
                    continue

        self._built = True


_INDEX = _LibraryIndex()


def _ensure_index() -> _LibraryIndex:
    _INDEX.build()
    return _INDEX


# ── Scoring / matching ───────────────────────────────────────────

def _normalize(s: str) -> str:
    """Lowercase, strip dashes/underscores/spaces for fuzzy comparison."""
    return re.sub(r"[\s\-_]", "", s.lower())


def _score_match(query_norm: str, candidate_norm: str, candidate_name: str) -> float:
    """Score: higher = better.  0 = no match."""
    if query_norm not in candidate_norm:
        return 0.0

    # Base score: fraction of candidate name covered by query
    coverage = len(query_norm) / max(len(candidate_norm), 1)

    # Bonus: exact match
    if query_norm == candidate_norm:
        return 100.0

    # Bonus: starts with query
    bonus = 10.0 if candidate_norm.startswith(query_norm) else 0.0

    # Penalty: very long candidate name (less likely to be the right one)
    length_penalty = max(0, (len(candidate_norm) - len(query_norm) - 20)) * 0.5

    return coverage * 50.0 + bonus - length_penalty


# ── Public API ───────────────────────────────────────────────────

def find_footprint(value: str, ref_prefix: str = "") -> Optional[LibraryMatch]:
    """
    Find the best KiCad footprint for a component value.

    Args:
        value: Component value string, e.g. "ESP32-C3", "DHT22", "ATmega328P"
        ref_prefix: Single-letter designator prefix (R, C, U, D, Q, J …)

    Returns:
        Best LibraryMatch, or None if nothing found.
    """
    idx = _ensure_index()
    query = _normalize(value)
    if len(query) < 2:
        return None

    # Library priority for certain prefixes (prefer relevant libraries)
    lib_priority: dict[str, float] = {}
    if ref_prefix in ("U",):
        for lib_kw in ("RF_Module", "MCU_", "Package_DIP", "Package_QFP", "Package_BGA",
                        "Package_SO", "Sensor"):
            lib_priority[lib_kw] = 5.0
    elif ref_prefix == "Q":
        lib_priority["Package_TO_SOT_THT"] = 5.0
        lib_priority["Package_TO_SOT_SMD"] = 3.0

    best: LibraryMatch | None = None
    best_score = 0.0

    for lib, name in idx.footprints:
        name_norm = _normalize(name)
        score = _score_match(query, name_norm, name)
        if score <= 0:
            continue

        # Library affinity bonus
        for kw, bonus in lib_priority.items():
            if kw.lower() in lib.lower():
                score += bonus

        # Prefer non-U suffix variants (e.g. WROOM-02 over WROOM-02U)
        if name.endswith("U") and not query.endswith("u"):
            score -= 2.0

        if score > best_score:
            best_score = score
            best = LibraryMatch(library=lib, name=name, score=score)

    return best


def find_symbol(value: str, ref_prefix: str = "") -> Optional[LibraryMatch]:
    """
    Find the best KiCad symbol for a component value.

    Args:
        value: Component value string, e.g. "ESP32-C3", "LM358", "2N3904"
        ref_prefix: Single-letter designator prefix

    Returns:
        Best LibraryMatch, or None if nothing found.
    """
    idx = _ensure_index()
    query = _normalize(value)
    if len(query) < 2:
        return None

    # Library priority hints
    lib_priority: dict[str, float] = {}
    if ref_prefix == "U":
        for kw in ("MCU_", "RF_Module", "Sensor", "Amplifier", "Timer", "Regulator"):
            lib_priority[kw] = 3.0
    elif ref_prefix == "Q":
        lib_priority["Transistor"] = 5.0

    best: LibraryMatch | None = None
    best_score = 0.0

    for lib, name in idx.symbols:
        name_norm = _normalize(name)
        score = _score_match(query, name_norm, name)
        if score <= 0:
            continue

        for kw, bonus in lib_priority.items():
            if kw.lower() in lib.lower():
                score += bonus

        if score > best_score:
            best_score = score
            best = LibraryMatch(library=lib, name=name, score=score)

    return best


def find_component(value: str, ref_prefix: str = "") -> tuple[str | None, str | None]:
    """
    Find both symbol and footprint for a component.

    Strategy:
      1. Direct filename match for footprint (works for modules like ESP32)
      2. Symbol name match
      3. If footprint not found directly, look up the symbol's default footprint
         from KiCad's symbol→footprint mapping (works for ATmega328, 2N3904, etc.)
      4. If best symbol has no footprint mapping, try all matching symbols

    Returns:
        (symbol_id, footprint_id) — either or both can be None.
    """
    idx = _ensure_index()

    sym_match = find_symbol(value, ref_prefix)
    fp_match = find_footprint(value, ref_prefix)

    sym_id = sym_match.full_id if sym_match and sym_match.score >= 5.0 else None
    fp_id = fp_match.full_id if fp_match and fp_match.score >= 5.0 else None

    # Bridge: if we found a symbol but no footprint, look up the symbol's default
    if sym_id and not fp_id:
        default_fp = idx.sym_to_fp.get(sym_id)
        if default_fp:
            fp_id = default_fp

    # Extended bridge: if still no footprint, search ALL matching symbols for one
    # that has a footprint mapping (e.g. LM358 has DFN variant with mapping)
    if not fp_id:
        query = _normalize(value)
        if len(query) >= 2:
            candidates: list[tuple[float, str, str]] = []
            for lib, name in idx.symbols:
                name_norm = _normalize(name)
                score = _score_match(query, name_norm, name)
                if score > 0:
                    full = f"{lib}:{name}"
                    fp = idx.sym_to_fp.get(full)
                    if fp:
                        candidates.append((score, full, fp))
            if candidates:
                candidates.sort(key=lambda x: -x[0])
                best_sym_id, best_fp = candidates[0][1], candidates[0][2]
                fp_id = best_fp
                if not sym_id:
                    sym_id = best_sym_id

    return sym_id, fp_id


def get_footprint_geometry(footprint_id: str) -> tuple[tuple[float, float], dict[str, tuple[float, float]]] | None:
    """
    Load a KiCad footprint and extract pad geometry.

    Returns:
        ((width, height), {pin_number: (cx_rel, cy_rel)})
        Positions are CENTER-RELATIVE (relative to pad centroid).
        Returns None if footprint cannot be loaded.
    """
    if pcbnew is None:
        return None

    if ":" not in footprint_id:
        return None

    lib, name = footprint_id.split(":", 1)

    # Try loading directly
    module = None
    try:
        module = pcbnew.FootprintLoad(lib, name)
    except Exception:
        pass

    if module is None:
        lib_dir = _FP_ROOT / f"{lib}.pretty"
        if lib_dir.exists():
            try:
                module = pcbnew.FootprintLoad(str(lib_dir), name)
            except Exception:
                pass

    if module is None:
        return None

    pads = list(module.Pads())
    if not pads:
        return None

    xs = [float(pcbnew.ToMM(p.GetPosition().x)) for p in pads]
    ys = [float(pcbnew.ToMM(p.GetPosition().y)) for p in pads]

    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    width = max(xs) - min(xs) + 2.0   # add margin
    height = max(ys) - min(ys) + 2.0

    pin_offsets: dict[str, tuple[float, float]] = {}
    for pad in pads:
        num = str(pad.GetNumber()).strip()
        if not num:
            continue
        px = float(pcbnew.ToMM(pad.GetPosition().x)) - cx
        py = float(pcbnew.ToMM(pad.GetPosition().y)) - cy
        if num not in pin_offsets:  # keep first occurrence for duplicate pad numbers
            pin_offsets[num] = (round(px, 3), round(py, 3))

    return (round(width, 2), round(height, 2)), pin_offsets
