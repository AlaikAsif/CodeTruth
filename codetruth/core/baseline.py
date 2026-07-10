"""Baseline mode: adopt CodeTruth on a codebase that already has dead code.

A gate that fails on *all* existing findings never gets turned on — teams
can't stop the world to clean up first. The baseline records today's findings
as accepted debt; from then on `codetruth scan --ci` fails only on **newly
introduced** provably-dead code, exactly how mypy/eslint baselines make
adoption possible.

    codetruth baseline ./repo            # accept current findings
    codetruth scan ./repo --ci           # fail only on NEW safe_to_delete

The baseline keys on symbol ids (module:qualname), so line churn doesn't
invalidate it. A symbol that was merely `likely_dead` at baseline time and
*becomes* `safe_to_delete` (e.g. its last caller was deleted) counts as new —
its deadness just became provable, which is precisely what a PR gate should
surface. Entries whose symbols are gone (or alive again) are reported as
resolved so the baseline can be refreshed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .models import Status

if TYPE_CHECKING:
    from .scanner import ScanResult

BASELINE_VERSION = 1
BASELINE_FILENAME = ".codetruth.baseline.json"

# Statuses recorded in a baseline: the whole review queue, so the file also
# serves as a point-in-time snapshot for diffing — not just the gate tier.
_CANDIDATE_STATUSES = (Status.SAFE_TO_DELETE, Status.LIKELY_DEAD,
                       Status.UNCERTAIN_DYNAMIC_RISK)


def default_path(repo: str | Path) -> Path:
    return Path(repo) / BASELINE_FILENAME


def write_baseline(result: "ScanResult", path: str | Path) -> dict:
    """Snapshot every current candidate finding as accepted."""
    findings = {r.symbol: r.status.value for r in result.records
                if r.status in _CANDIDATE_STATUSES}
    doc = {
        "version": BASELINE_VERSION,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "language": result.language,
        "note": "Accepted findings. `codetruth scan --ci` fails only on "
                "provably-dead symbols not accepted here. Regenerate with "
                "`codetruth baseline` after a cleanup.",
        "findings": dict(sorted(findings.items())),
    }
    p = Path(path)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def load_baseline(path: str | Path) -> Optional[dict]:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if doc.get("version") != BASELINE_VERSION \
            or not isinstance(doc.get("findings"), dict):
        return None
    return doc


@dataclass
class BaselineDiff:
    new_safe: list = field(default_factory=list)      # EvidenceRecord — gate fails on these
    new_candidates: list = field(default_factory=list)  # records newly in queue (info)
    resolved: list = field(default_factory=list)      # baseline symbols now gone/alive


def diff_against_baseline(result: "ScanResult", baseline: dict) -> BaselineDiff:
    accepted: dict = baseline.get("findings", {})
    diff = BaselineDiff()
    current_candidates = set()
    for rec in result.records:
        if rec.status not in _CANDIDATE_STATUSES:
            continue
        current_candidates.add(rec.symbol)
        prev = accepted.get(rec.symbol)
        if rec.status is Status.SAFE_TO_DELETE \
                and prev != Status.SAFE_TO_DELETE.value:
            # brand-new dead code, or previously-hedged code whose deadness
            # became provable — either way, the PR gate should surface it
            diff.new_safe.append(rec)
        elif prev is None:
            diff.new_candidates.append(rec)
    for symbol in accepted:
        if symbol not in current_candidates:
            diff.resolved.append(symbol)
    return diff
