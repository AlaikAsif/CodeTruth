"""SARIF 2.1.0 output — the format GitHub Code Scanning ingests, so CI
findings show up as inline PR annotations with the evidence attached.

Only review-queue candidates are emitted (definitely_used would be noise).
Severity mapping keeps the advisory posture: provably-dead code is a
`warning` (the gate tier); the review tiers are `note`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Status

if TYPE_CHECKING:
    from .scanner import ScanResult

_RULES = {
    Status.SAFE_TO_DELETE: {
        "id": "codetruth/safe-to-delete",
        "level": "warning",
        "name": "SafeToDelete",
        "short": "Provably dead code",
        "full": "No usage path found under any analysis rule, and the "
                "symbol's name appears nowhere else in the repository. "
                "Review and delete, mark it as an entrypoint, or accept it "
                "into the baseline.",
    },
    Status.LIKELY_DEAD: {
        "id": "codetruth/likely-dead",
        "level": "note",
        "name": "LikelyDead",
        "short": "No usage found; external exposure can't be ruled out",
        "full": "No reference was found, but the symbol is public API, a "
                "module, or test-only — consumers outside this repository "
                "cannot be ruled out.",
    },
    Status.UNCERTAIN_DYNAMIC_RISK: {
        "id": "codetruth/uncertain-dynamic-risk",
        "level": "note",
        "name": "UncertainDynamicRisk",
        "short": "Weak evidence of dynamic usage",
        "full": "String references, reflection targets, or name matches "
                "suggest this symbol may be reached dynamically.",
    },
}


def to_sarif(result: "ScanResult") -> dict:
    from .. import __version__

    results = []
    for rec in result.candidates():
        rule = _RULES.get(rec.status)
        if rule is None:
            continue
        lines = [f"{rec.symbol} — {rec.status.value} "
                 f"(rank {rec.rank_score:.2f})."]
        if rec.evidence_for_deletion:
            lines.append("Evidence for deletion: "
                         + "; ".join(rec.evidence_for_deletion[:4]))
        if rec.evidence_against_deletion:
            lines.append("Evidence against: "
                         + "; ".join(rec.evidence_against_deletion[:4]))
        results.append({
            "ruleId": rule["id"],
            "level": rule["level"],
            "message": {"text": "\n".join(lines)},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": rec.file.replace("\\", "/"),
                                         "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": max(rec.line, 1)},
                },
            }],
            # stable across line churn: keyed on the symbol id
            "partialFingerprints": {"codetruthSymbol/v1": rec.symbol},
        })

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/"
                   "master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "CodeTruth",
                "informationUri": "https://github.com/AlaikAsif/CodeTruth",
                "version": __version__,
                "rules": [{
                    "id": r["id"],
                    "name": r["name"],
                    "shortDescription": {"text": r["short"]},
                    "fullDescription": {"text": r["full"]},
                    "defaultConfiguration": {"level": r["level"]},
                } for r in _RULES.values()],
            }},
            "results": results,
        }],
    }


def write_sarif(result: "ScanResult", path: str | Path) -> None:
    Path(path).write_text(json.dumps(to_sarif(result), indent=2),
                          encoding="utf-8")
