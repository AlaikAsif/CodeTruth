"""Orchestrator: repo path in, ScanResult out. Runs all four layers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import cache
from .config import load_config
from .deletion import build_deletion_plan
from .evidence import build_records
from .graph import CodeGraph
from .models import (Action, EvidenceRecord, Marker, MarkerKind, RiskLevel,
                     Status, Symbol)
from .plugin import RuleContext, get_plugin


@dataclass
class ScanResult:
    repo_path: str
    language: str
    records: list[EvidenceRecord]
    symbol_count: int
    edge_count: int
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        counts = {s.value: 0 for s in Status}
        for r in self.records:
            counts[r.status.value] += 1
        return {
            "repo": self.repo_path, "language": self.language,
            "symbols": self.symbol_count, "edges": self.edge_count,
            "status_counts": counts, "warnings": len(self.warnings),
        }

    def candidates(self) -> list[EvidenceRecord]:
        """Everything not proven used — the review queue. Ordered by status
        tier, then by rank_score (strongest deletion targets first within a
        tier), so the top of the list is where an agent should look first."""
        order = {Status.SAFE_TO_DELETE: 0, Status.LIKELY_DEAD: 1,
                 Status.UNCERTAIN_DYNAMIC_RISK: 2}
        return sorted((r for r in self.records
                       if r.status is not Status.DEFINITELY_USED),
                      key=lambda r: (order[r.status], -r.rank_score,
                                     r.file, r.line))

    def find(self, query: str) -> list[EvidenceRecord]:
        """Locate records by exact id, dotted path, or trailing name match."""
        exact = [r for r in self.records if r.symbol == query]
        if exact:
            return exact
        norm = query.replace(":", ".")
        dotted = [r for r in self.records
                  if r.symbol.replace(":", ".") == norm]
        if dotted:
            return dotted
        return [r for r in self.records
                if r.symbol.replace(":", ".").endswith("." + norm)
                or r.name == query]

    def to_dict(self, include_used: bool = True) -> dict:
        records = self.records if include_used else self.candidates()
        return {
            "summary": self.summary(),
            "records": [r.to_dict() for r in records],
            "warnings": self.warnings,
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2),
                              encoding="utf-8")


def _verify_safe_candidates(repo: Path, records: list[EvidenceRecord],
                            modules: list, config_files: list[Path],
                            symbols: list[Symbol]) -> None:
    """Independent textual audit of every safe_to_delete verdict.

    The graph proves no *structural* usage path. This pass additionally
    requires the symbol's name to appear nowhere else in the repository's
    text — source, comments, docstrings, or config. Any stray occurrence is
    a possible usage path, so the verdict is demoted to
    uncertain_dynamic_risk. This makes the core guarantee mechanical:
    safe_to_delete is only ever emitted when no usage path can be found.
    """
    candidates = [r for r in records if r.status is Status.SAFE_TO_DELETE]
    if not candidates:
        return
    symbols_by_id = {s.id: s for s in symbols}

    texts: dict[str, str] = {}
    for m in modules:
        try:
            texts[m.rel_path] = Path(m.abs_path).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
    for path in config_files:
        try:
            texts[path.relative_to(repo).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue

    for rec in candidates:
        pattern = re.compile(rf"\b{re.escape(rec.name)}\b")
        end_line = symbols_by_id[rec.symbol].end_line
        hit = _find_external_occurrence(rec, end_line, pattern, texts)
        if hit is None:
            rec.evidence_for_deletion.append(
                "Verified: symbol name occurs nowhere else in the "
                "repository's text (source, comments, or config)")
            continue
        rec.status = Status.UNCERTAIN_DYNAMIC_RISK
        rec.risk_level = RiskLevel.HIGH
        rec.recommended_action = Action.REVIEW_REQUIRED
        # A bare text mention (comment/docstring/config) is weak evidence of
        # use, so this ranks near the top of the uncertain tier — but it must
        # no longer carry its former safe-tier score.
        rec.rank_score = 0.40
        rec.evidence_against_deletion.append(
            f"Text occurrence outside the definition at {hit} — possible "
            "usage path the graph cannot classify")


def _find_external_occurrence(rec: EvidenceRecord, end_line: int,
                              pattern: re.Pattern,
                              texts: dict[str, str]) -> Optional[str]:
    """First occurrence of the symbol's name outside its own definition
    span, as 'file:line' — or None if the name appears nowhere else.
    The span in the home file (decorators through end of block) is 'self';
    anything beyond it, in any file, counts as a possible usage path."""
    for rel, text in texts.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            if not pattern.search(line):
                continue
            if rel == rec.file and rec.line <= lineno <= end_line:
                continue
            return f"{rel}:{lineno}"
    return None


def _related_tests(symbol: str, symbols_by_id: dict, graph: CodeGraph,
                   limit: int = 10) -> list[str]:
    """Test files a human should run after removing this symbol: tests that
    reference the symbol directly, plus tests importing its module. Advisory
    context on the deletion plan — CodeTruth never runs them itself."""
    sym = symbols_by_id.get(symbol)
    targets = [symbol] + ([sym.module] if sym and sym.module != symbol else [])
    files: list[str] = []
    for target in targets:
        for edge in graph.inbound(target):
            src = symbols_by_id.get(edge.src)
            if src is not None and src.is_test and src.file not in files:
                files.append(src.file)
    return sorted(files)[:limit]


def scan_repo(repo_path: str | Path, language: str = "python",
              treat_public_as_api: Optional[bool] = None,
              runtime_log: Optional[str | Path] = None,
              use_cache: bool = True,
              reachability: str = "default",
              progress=None) -> ScanResult:
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"Not a directory: {repo}")
    if reachability not in ("default", "strict"):
        raise ValueError("reachability must be 'default' or 'strict'")

    plugin = get_plugin(language)
    repo_cfg = load_config(repo)
    if treat_public_as_api is None:
        # .codetruth.toml app_mode=true means "application code":
        # public symbols are internal.
        treat_public_as_api = not repo_cfg.app_mode if repo_cfg.app_mode is not None \
            else True

    ignores = tuple(repo_cfg.ignore_paths)

    # Persistent cache: a scan is a pure function of the source+config bytes
    # (the .codetruth.toml is fingerprinted too). Runtime logs are not part
    # of the fingerprint, so bypass the cache when one is supplied.
    fp = None
    if use_cache and not runtime_log and hasattr(plugin, "source_files"):
        fp = cache.fingerprint(repo, plugin.source_files(repo, ignores))
        cached = cache.load(repo, language, treat_public_as_api, fp,
                            reachability)
        if cached is not None:
            return cached

    def _tick(phase: str, done: int = 0, total: int = 0, detail: str = ""):
        if progress is not None:
            progress(phase, done, total, detail)

    # Layer 1 — symbols
    modules, warnings = plugin.extract(repo, ignores, progress=progress)
    index = plugin.build_index(modules)
    symbols: list[Symbol] = plugin.symbols(modules)

    # Layer 2 — relationship graph
    _tick("edges")
    graph = CodeGraph()
    markers: list[Marker] = []
    plugin.build_edges(repo, modules, index, graph, markers)

    # Layer 3 — semantic safety rules
    _tick("rules")
    config_files = plugin.config_files(repo, ignores) \
        if hasattr(plugin, "config_files") else []
    ctx = RuleContext(repo_path=repo, modules=modules, index=index,
                      graph=graph, markers=markers, config_files=config_files)
    for rule in plugin.rules():
        rule.apply(ctx)

    # Phase 6 — runtime evidence (strongest tier when present)
    if runtime_log:
        from ..runtime import load_runtime_markers
        markers.extend(load_runtime_markers(runtime_log, {s.id for s in symbols}))

    # Layer 4 — evidence + decision
    _tick("classify")
    records = build_records(symbols, graph, markers,
                            treat_public_as_api=treat_public_as_api,
                            reachability=reachability)

    # Final backstop: never emit safe_to_delete when ANY usage path exists —
    # including ones the AST can't see (comments, docstrings, templates).
    _tick("verify")
    _verify_safe_candidates(repo, records, modules, config_files, symbols)

    # Attach advisory deletion plans to the records that earned one.
    symbols_by_id = {s.id: s for s in symbols}
    for rec in records:
        if rec.status is Status.SAFE_TO_DELETE:
            try:
                rec.deletion_plan = build_deletion_plan(repo,
                                                        symbols_by_id[rec.symbol])
                if rec.deletion_plan is not None:
                    tests = _related_tests(rec.symbol, symbols_by_id, graph)
                    if tests:
                        rec.deletion_plan["related_tests"] = tests
            except Exception:  # a plan is advice; never fail the scan for it
                rec.deletion_plan = None

    result = ScanResult(
        repo_path=str(repo), language=language, records=records,
        symbol_count=len(symbols), edge_count=len(graph.edges),
        warnings=warnings,
    )
    if fp is not None:
        cache.save(repo, result, language, treat_public_as_api, fp,
                   reachability)
    return result
