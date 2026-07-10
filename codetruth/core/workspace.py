"""Workspace (multi-repo) scanning — the cross-service answer.

A symbol can look completely dead inside its own repo yet be reached every
minute from another service: an HTTP endpoint called over the wire, a shared
package imported by a sibling repo, a handler named by a string in config.
`scan_workspace` scans each repo normally, then overlays cross-repo evidence
so those symbols are never recommended for deletion.

Overlay is conservative and additive. It can only move a candidate toward
"keep": a `safe_to_delete` or `likely_dead` symbol reached from another repo
is raised to `uncertain_dynamic_risk` with an explicit cross-repo reason. It
never downgrades a `definitely_used` verdict and never makes anything *more*
deletable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import crossrepo
from .models import Action, RiskLevel, Status
from .scanner import ScanResult, scan_repo


@dataclass
class CrossRef:
    symbol: str          # provider symbol id
    repo: str            # provider repo label
    reason: str
    from_repo: str       # consumer repo label

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "repo": self.repo,
                "reason": self.reason, "from_repo": self.from_repo}


@dataclass
class WorkspaceResult:
    repos: dict           # label -> ScanResult
    crossrefs: list       # list[CrossRef]

    def summary(self) -> dict:
        per = {label: r.summary()["status_counts"]
               for label, r in self.repos.items()}
        return {"repos": list(self.repos), "per_repo_status": per,
                "cross_references": len(self.crossrefs)}

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "repos": {label: r.to_dict(include_used=False)
                      for label, r in self.repos.items()},
            "cross_references": [c.to_dict() for c in self.crossrefs],
        }

    def candidates(self):
        """(repo_label, record) for every non-used candidate across repos,
        cross-repo-reached ones sorted last (safest to keep)."""
        out = []
        for label, r in self.repos.items():
            for rec in r.candidates():
                out.append((label, rec))
        return out


def _label_for(path: Path, taken: set) -> str:
    label = path.name or "repo"
    base, n = label, 2
    while label in taken:
        label, n = f"{base}-{n}", n + 1
    taken.add(label)
    return label


def scan_workspace(repo_paths, language: str = "python",
                   treat_public_as_api=None, use_cache: bool = True,
                   reachability: str = "default") -> WorkspaceResult:
    paths = [Path(p).resolve() for p in repo_paths]
    labels: dict = {}
    taken: set = set()
    results: dict = {}
    for p in paths:
        if not p.is_dir():
            raise FileNotFoundError(f"Not a directory: {p}")
        label = _label_for(p, taken)
        labels[label] = p
        results[label] = scan_repo(p, language=language,
                                   treat_public_as_api=treat_public_as_api,
                                   use_cache=use_cache,
                                   reachability=reachability)

    if language != "python":
        # Cross-repo surface extraction is Python-only for now; imports/routes
        # for other languages are a follow-up. Still return per-repo results.
        return WorkspaceResult(results, [])

    surfaces = {label: crossrepo.extract_surface_python(label, labels[label],
                                                        results[label])
                for label in labels}

    crossrefs = _link(surfaces)
    _overlay(results, crossrefs)
    return WorkspaceResult(results, crossrefs)


def _link(surfaces: dict) -> list:
    """Match consumers in one repo to providers in another."""
    crossrefs: list = []

    # 1. HTTP route ↔ client call
    for c_label, c_surf in surfaces.items():
        for call in c_surf.consumed:
            for p_label, p_surf in surfaces.items():
                if p_label == c_label:
                    continue
                for route in p_surf.provided:
                    if not route.handler:
                        continue
                    if route.method not in (call.method, "any"):
                        continue
                    if crossrepo.paths_match(route.path, call.path):
                        crossrefs.append(CrossRef(
                            route.handler, p_label,
                            f"HTTP {call.method.upper()} {call.raw!r} in "
                            f"repo '{c_label}' ({call.file}:{call.line}) "
                            f"matches route {route.raw!r} — called across "
                            "services", c_label))

    # 2. Cross-repo imports of a shared package: repo C imports a module or
    #    symbol that repo P defines (monorepo shared lib / installed sibling).
    for c_label, c_surf in surfaces.items():
        for p_label, p_surf in surfaces.items():
            if p_label == c_label:
                continue
            # `from shared import fn` — credit the specific symbol.
            for mod, name in c_surf.imported_symbols:
                if mod in p_surf.modules:
                    sid = p_surf.exported_names.get(name) \
                        or f"{mod}:{name}"
                    crossrefs.append(CrossRef(
                        sid, p_label,
                        f"'{name}' imported from '{mod}' by repo "
                        f"'{c_label}' — shared across repos", c_label))
            # `import shared` / `from shared import *` — credit the module.
            for imp in c_surf.imported:
                if imp in p_surf.modules:
                    crossrefs.append(CrossRef(
                        imp, p_label,
                        f"module '{imp}' imported by repo '{c_label}' — "
                        "shared across repos", c_label))

    # 3. Cross-repo string/name references (config-driven dispatch across repos)
    all_consumed_names: dict = {}
    for label, surf in surfaces.items():
        for name in _string_tokens(surf):
            all_consumed_names.setdefault(name, set()).add(label)
    for p_label, p_surf in surfaces.items():
        for name, sid in p_surf.exported_names.items():
            if len(name) < 4:
                continue
            users = all_consumed_names.get(name, set()) - {p_label}
            for u in users:
                crossrefs.append(CrossRef(
                    sid, p_label,
                    f"exported name '{name}' appears as a string/reference in "
                    f"repo '{u}' — possible cross-repo use", u))
    return crossrefs


def _string_tokens(surf) -> set:
    """Symbol-like string tokens a repo references (from its consumed URLs and
    raw route names). A light signal for config-driven cross-repo wiring."""
    tokens: set = set()
    for r in surf.consumed + surf.provided:
        for seg in r.raw.replace(":", "/").split("/"):
            if seg.isidentifier():
                tokens.add(seg)
    return tokens


def _overlay(results: dict, crossrefs: list) -> None:
    by_symbol: dict = {}
    for c in crossrefs:
        by_symbol.setdefault((c.repo, c.symbol), []).append(c)

    for (repo, symbol), refs in by_symbol.items():
        result = results.get(repo)
        if result is None:
            continue
        for rec in result.records:
            if rec.symbol != symbol:
                continue
            for c in refs:
                rec.evidence_against_deletion.append(
                    "Cross-repo: " + c.reason)
            # Never recommend deleting something another service reaches.
            if rec.status in (Status.SAFE_TO_DELETE, Status.LIKELY_DEAD):
                rec.status = Status.UNCERTAIN_DYNAMIC_RISK
                rec.risk_level = RiskLevel.HIGH
                rec.recommended_action = Action.REVIEW_REQUIRED
                rec.rank_score = min(rec.rank_score, 0.35)
            break
