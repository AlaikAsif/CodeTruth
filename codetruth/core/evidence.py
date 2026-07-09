"""Layer 4 — evidence assembly and the 4-way classification.

The logic inversion lives here: a symbol is only `safe_to_delete` when the
system FAILS to find any usage path — no strong edge, no weak edge, no
framework marker, no dynamic access in its module, and no public-API
exposure that static analysis can't rule out.
"""
from __future__ import annotations

from collections import defaultdict

from .graph import CodeGraph
from .models import (Action, Edge, EdgeKind, EvidenceRecord, Marker,
                     MarkerKind, RiskLevel, Status, Symbol, SymbolType)

MAX_EVIDENCE_ITEMS = 8

# How strongly one weak inbound edge argues *against* deletion, by kind.
# Attribute name-matches are the noisiest signal (a bare `.name` matches every
# symbol called `name`); a string-literal or reflection reference is far more
# likely to be a real dynamic use. These weights spread the otherwise-flat
# uncertain_dynamic_risk bucket so an agent can triage it.
_WEAK_WEIGHT = {
    EdgeKind.ATTRIBUTE: 0.15,
    EdgeKind.REFERENCE: 0.30,
    EdgeKind.CALL: 0.30,
    EdgeKind.IMPORT: 0.40,
    EdgeKind.STRING_REF: 0.50,
    EdgeKind.DYNAMIC: 0.50,
    EdgeKind.INHERIT: 0.60,
}


def _rank_score(status: Status, weak: list[Edge], cautions: list,
                runtime_zero: list, in_dynamic_module: bool,
                strong_test: list, is_public_api: bool,
                treat_public_as_api: bool, sym: Symbol) -> float:
    """Ordering key in [0, 1]; higher = review/delete first. Deterministic
    heuristic, not a calibrated probability (PLAN.md §4)."""
    if status is Status.DEFINITELY_USED:
        return 0.0
    if status is Status.SAFE_TO_DELETE:
        # Runtime tracing that observed zero calls is the strongest signal.
        return 1.0 if runtime_zero else 0.95
    if status is Status.LIKELY_DEAD:
        c = 0.75
        if is_public_api and treat_public_as_api:
            c -= 0.10   # could be imported by an external consumer
        if sym.type is SymbolType.MODULE:
            c -= 0.10   # could be an external entry point (cron/script)
        if strong_test:
            c -= 0.10   # its own test suite still references it
        return round(max(0.50, c), 3)
    # UNCERTAIN_DYNAMIC_RISK: rank by how much weak evidence exists.
    pressure = sum(_WEAK_WEIGHT.get(e.kind, 0.30) for e in weak)
    pressure += 0.40 * len(cautions)
    if in_dynamic_module:
        pressure += 0.50
    if strong_test:
        pressure += 0.20
    return round(max(0.10, min(0.45, 0.45 / (1.0 + pressure))), 3)


def _edge_is_from_test(edge: Edge, symbols_by_id: dict[str, Symbol]) -> bool:
    src = symbols_by_id.get(edge.src)
    if src is not None:
        return src.is_test
    # Pseudo-source ("file:...") — treat config references as non-test.
    return False


def build_records(symbols: list[Symbol], graph: CodeGraph,
                  markers: list[Marker],
                  treat_public_as_api: bool = True) -> list[EvidenceRecord]:
    symbols_by_id = {s.id: s for s in symbols}
    markers_by_symbol: dict[str, list[Marker]] = defaultdict(list)
    dynamic_modules: set[str] = set()
    for m in markers:
        markers_by_symbol[m.symbol].append(m)
        if m.kind is MarkerKind.DYNAMIC_MODULE:
            dynamic_modules.add(m.symbol)

    records = []
    for sym in symbols:
        records.append(_classify(sym, graph, markers_by_symbol.get(sym.id, []),
                                 dynamic_modules, symbols_by_id,
                                 treat_public_as_api))
    return records


def _classify(sym: Symbol, graph: CodeGraph, sym_markers: list[Marker],
              dynamic_modules: set[str], symbols_by_id: dict[str, Symbol],
              treat_public_as_api: bool) -> EvidenceRecord:
    strong, weak = graph.inbound_split(sym.id)
    strong_nontest = [e for e in strong if not _edge_is_from_test(e, symbols_by_id)]
    strong_test = [e for e in strong if _edge_is_from_test(e, symbols_by_id)]

    entry = [m for m in sym_markers if m.kind in (MarkerKind.ENTRYPOINT,
                                                  MarkerKind.RUNTIME_USED)]
    cautions = [m for m in sym_markers if m.kind is MarkerKind.CAUTION]
    runtime_zero = [m for m in sym_markers if m.kind is MarkerKind.RUNTIME_ZERO]
    in_dynamic_module = sym.module in dynamic_modules

    for_del: list[str] = []
    against: list[str] = []

    # ---- evidence both ways, assembled regardless of final status ----------
    if entry:
        against.extend(m.reason for m in entry[:MAX_EVIDENCE_ITEMS])
    if strong_nontest:
        against.append(f"{len(strong_nontest)} strong reference(s), e.g. "
                       + strong_nontest[0].describe())
    if strong_test and not strong_nontest:
        against.append(f"{len(strong_test)} reference(s) from tests only, e.g. "
                       + strong_test[0].describe())
    for e in weak[:MAX_EVIDENCE_ITEMS]:
        against.append("Possible dynamic usage: " + e.describe())
    if len(weak) > MAX_EVIDENCE_ITEMS:
        against.append(f"...and {len(weak) - MAX_EVIDENCE_ITEMS} more weak reference(s)")
    for m in cautions[:MAX_EVIDENCE_ITEMS]:
        against.append(m.reason)
    if in_dynamic_module:
        against.append(f"module {sym.module} contains non-literal dynamic access "
                       "(getattr/eval/import_module) — usage cannot be ruled out")

    if not strong:
        for_del.append("No strong references (calls/imports/inheritance) found "
                       "in the repository")
    if not weak:
        for_del.append("No string-literal, reflection, or attribute-name "
                       "references detected")
    if not entry:
        for_del.append("Not matched by any framework/entry-point rule")
    if not strong_test and not sym.is_test:
        for_del.append("Not referenced by the test suite")
    for m in runtime_zero:
        for_del.append(m.reason)

    is_public_api = sym.is_public and sym.type is not SymbolType.MODULE
    if is_public_api and treat_public_as_api:
        against.append("Symbol is public — may be consumed outside this "
                       "repository (pass treat_public_as_api=False for "
                       "application code)")

    # ---- decision ladder (conservative by construction) --------------------
    if entry:
        status = Status.DEFINITELY_USED
    elif strong_nontest:
        status = Status.DEFINITELY_USED
    elif strong_test:
        # Only its own tests keep it alive.
        status = Status.UNCERTAIN_DYNAMIC_RISK if weak else Status.LIKELY_DEAD
    elif weak:
        status = Status.UNCERTAIN_DYNAMIC_RISK
    elif cautions or in_dynamic_module:
        status = Status.UNCERTAIN_DYNAMIC_RISK
    elif sym.type is SymbolType.MODULE:
        # An unimported module might still be an external entry point
        # (cron, script, service runner) — never provably dead statically.
        status = Status.LIKELY_DEAD
        against.append("Modules can be executed directly or referenced "
                       "externally — static analysis cannot prove otherwise")
    elif is_public_api and treat_public_as_api:
        status = Status.LIKELY_DEAD
    else:
        status = Status.SAFE_TO_DELETE

    risk, action = {
        Status.DEFINITELY_USED: (RiskLevel.LOW, Action.KEEP),
        Status.SAFE_TO_DELETE: (RiskLevel.LOW, Action.DELETE),
        Status.LIKELY_DEAD: (RiskLevel.MEDIUM, Action.REVIEW_REQUIRED),
        Status.UNCERTAIN_DYNAMIC_RISK: (RiskLevel.HIGH, Action.REVIEW_REQUIRED),
    }[status]

    rank = _rank_score(status, weak, cautions, runtime_zero, in_dynamic_module,
                       strong_test, is_public_api, treat_public_as_api, sym)

    return EvidenceRecord(
        symbol=sym.id, name=sym.name, type=sym.type, file=sym.file,
        line=sym.line, status=status, risk_level=risk,
        recommended_action=action, evidence_for_deletion=for_del,
        evidence_against_deletion=against, inbound_strong=len(strong),
        inbound_weak=len(weak), exported=sym.exported, rank_score=rank,
    )
