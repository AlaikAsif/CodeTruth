"""Layer 4 — evidence assembly and the 4-way classification.

The logic inversion lives here: a symbol is only `safe_to_delete` when the
system FAILS to find any usage path — no strong edge, no weak edge, no
framework marker, no dynamic access in its module, and no public-API
exposure that static analysis can't rule out.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .graph import CodeGraph
from .models import (Action, Edge, EdgeKind, EdgeStrength, EvidenceRecord,
                     Marker, MarkerKind, RiskLevel, Status, Symbol, SymbolType)

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


def _compute_live(symbols: list[Symbol], graph: CodeGraph,
                  markers_by_symbol: dict[str, list[Marker]],
                  treat_public_as_api: bool,
                  reachability: str = "default") -> set[str]:
    """Reachability: the set of symbols provably executable from a live root.

    Default mode roots: modules (their top-level code runs on import),
    framework/test/runtime entrypoints, and — in library mode — every public
    symbol (an external consumer could call it).

    Strict mode ("useless clump" detection) roots are ONLY real entry points:
    framework routes/commands, __main__ modules, tests, runtime observations,
    and entrypoints declared in .codetruth.toml. Code that is internally
    well-connected but never reached from any entry point — an orphaned
    island — then surfaces in the review queue. Modules are not automatic
    roots in strict mode; a module is live only if something live imports it
    or a rule marked it (e.g. a __main__ guard).

    Liveness propagates along STRONG edges only; a strong reference from
    unreachable code proves nothing, which is exactly the dead-cluster case.
    Weak edges never propagate liveness; they feed the uncertainty tier
    directly regardless of their source, which stays conservative.
    """
    strict = reachability == "strict"
    live: set[str] = set()
    for s in symbols:
        if any(m.kind in (MarkerKind.ENTRYPOINT, MarkerKind.RUNTIME_USED)
               for m in markers_by_symbol.get(s.id, ())):
            live.add(s.id)
        elif not strict and s.type is SymbolType.MODULE:
            live.add(s.id)
        elif not strict and treat_public_as_api and s.is_public:
            live.add(s.id)

    stack = list(live)
    while stack:
        src = stack.pop()
        if src not in graph.g:
            continue
        for _src, dst, data in graph.g.out_edges(src, data=True):
            if data["edge"].strength is EdgeStrength.STRONG and dst not in live:
                live.add(dst)
                stack.append(dst)
    return live


def _dead_clusters(symbols: list[Symbol], graph: CodeGraph,
                   live: set[str]) -> dict[str, list[str]]:
    """Group unreachable symbols into connected clumps. Two dead symbols
    joined by a strong edge belong to the same island; reporting them as a
    unit ('delete as a group') is far more actionable than scattered rows."""
    dead = {s.id for s in symbols
            if s.id not in live and s.type is not SymbolType.MODULE}
    if not dead:
        return {}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.strength is EdgeStrength.STRONG and e.src in dead and e.dst in dead:
            adjacency[e.src].add(e.dst)
            adjacency[e.dst].add(e.src)
    clusters: dict[str, list[str]] = {}
    seen: set[str] = set()
    for start in adjacency:
        if start in seen:
            continue
        component, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        seen |= component
        if len(component) > 1:
            members = sorted(component)
            for node in component:
                clusters[node] = members
    return clusters


def build_records(symbols: list[Symbol], graph: CodeGraph,
                  markers: list[Marker],
                  treat_public_as_api: bool = True,
                  reachability: str = "default") -> list[EvidenceRecord]:
    symbols_by_id = {s.id: s for s in symbols}
    markers_by_symbol: dict[str, list[Marker]] = defaultdict(list)
    dynamic_modules: set[str] = set()
    for m in markers:
        markers_by_symbol[m.symbol].append(m)
        if m.kind is MarkerKind.DYNAMIC_MODULE:
            dynamic_modules.add(m.symbol)

    live = _compute_live(symbols, graph, markers_by_symbol,
                         treat_public_as_api, reachability)
    clusters = _dead_clusters(symbols, graph, live)

    records = []
    for sym in symbols:
        records.append(_classify(sym, graph, markers_by_symbol.get(sym.id, []),
                                 dynamic_modules, symbols_by_id,
                                 treat_public_as_api, live,
                                 clusters.get(sym.id), reachability))
    return records


def _classify(sym: Symbol, graph: CodeGraph, sym_markers: list[Marker],
              dynamic_modules: set[str], symbols_by_id: dict[str, Symbol],
              treat_public_as_api: bool, live: set[str],
              cluster: Optional[list[str]] = None,
              reachability: str = "default") -> EvidenceRecord:
    strong, weak = graph.inbound_split(sym.id)
    strong_nontest = [e for e in strong if not _edge_is_from_test(e, symbols_by_id)]
    strong_test = [e for e in strong if _edge_is_from_test(e, symbols_by_id)]
    # A strong reference only proves use when its source is itself reachable.
    # Unknown sources (not in the symbol table) are treated as live — never
    # assume deadness about code we can't see.
    strong_live = [e for e in strong_nontest
                   if e.src in live or e.src not in symbols_by_id]
    strong_deadsrc = [e for e in strong_nontest
                      if e.src in symbols_by_id and e.src not in live]

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
    if strong_live:
        against.append(f"{len(strong_live)} strong reference(s), e.g. "
                       + strong_live[0].describe())
    if strong_deadsrc and not strong_live:
        srcs = sorted({e.src for e in strong_deadsrc})
        against.append(
            f"{len(strong_deadsrc)} strong reference(s) exist, but only from "
            f"code that is itself unreachable ({', '.join(srcs[:4])}) — "
            "deleting this symbol alone would break that dead code; delete "
            "the cluster together")
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
    elif not strong_live and not strong_test and strong_deadsrc:
        for_del.append("Every strong reference originates from unreachable "
                       "(dead) code — no live usage path exists")
    if cluster:
        others = [c for c in cluster if c != sym.id]
        for_del.append(
            f"Part of an unreachable {len(cluster)}-symbol cluster with "
            f"{', '.join(others[:6])}{'…' if len(others) > 6 else ''} — the "
            "clump can be reviewed and deleted as a group")
    if reachability == "strict" and sym.id not in live \
            and sym.type is not SymbolType.MODULE and not entry:
        for_del.append("Strict mode: not reachable from any entry point "
                       "(route, command, __main__, test, declared entrypoint)")
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
    elif strong_live:
        status = Status.DEFINITELY_USED
    elif strong_test:
        # Only its own tests keep it alive.
        status = Status.UNCERTAIN_DYNAMIC_RISK if weak else Status.LIKELY_DEAD
    elif weak:
        status = Status.UNCERTAIN_DYNAMIC_RISK
    elif cautions or in_dynamic_module:
        status = Status.UNCERTAIN_DYNAMIC_RISK
    elif strong_deadsrc:
        # Dead-cluster interior: no live path reaches it, but deleting it
        # alone would break its (dead) referrers. Never safe standalone —
        # the advice is to review and delete the cluster as a group.
        status = Status.LIKELY_DEAD
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
        cluster=list(cluster) if cluster else None,
    )
