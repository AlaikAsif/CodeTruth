"""Recall/precision regression on a repo with construction-known ground truth.

Guards the two numbers that matter (PLAN.md §9):
  - zero false positives: no genuinely-used symbol is ever safe_to_delete;
  - recall: genuinely-dead symbols are surfaced for deletion.

Ground truth lives in tests/validation/labels.json (kept outside the scanned
app/ tree so it can't pollute string-reference detection).
"""
import json
from pathlib import Path

import pytest

from codetruth import scan

VALIDATION = Path(__file__).parent / "validation"


@pytest.fixture(scope="module")
def labeled():
    spec = json.loads((VALIDATION / "labels.json").read_text(encoding="utf-8"))
    result = scan(VALIDATION / spec["repo"],
                  treat_public_as_api=spec["treat_public_as_api"],
                  use_cache=False)
    by_id = {r.symbol: r for r in result.records}
    return spec["labels"], by_id


def test_no_false_positives(labeled):
    """The core guarantee: nothing labeled 'used' is ever safe_to_delete."""
    labels, by_id = labeled
    offenders = [s for s, lab in labels.items()
                 if lab == "used" and s in by_id
                 and by_id[s].status.value == "safe_to_delete"]
    assert offenders == []


def test_all_labeled_symbols_exist(labeled):
    labels, by_id = labeled
    missing = [s for s in labels if s not in by_id]
    assert missing == [], f"labels reference unknown symbols: {missing}"


def test_recall_meets_threshold(labeled):
    """Most genuinely-dead symbols reach safe_to_delete; the rest stay in the
    review queue. No dead symbol should be far off — only dead-cluster
    interiors may hide as definitely_used."""
    labels, by_id = labeled
    dead = [s for s, lab in labels.items() if lab == "dead"]
    safe = [s for s in dead if by_id[s].status.value == "safe_to_delete"]
    assert len(safe) / len(dead) >= 0.80


def test_no_dead_symbol_ranked_used(labeled):
    """Dead-cluster elimination: no genuinely-dead symbol may hide as
    definitely_used — every one must appear in the review queue."""
    labels, by_id = labeled
    stuck = [s for s, lab in labels.items()
             if lab == "dead" and by_id[s].status.value == "definitely_used"]
    assert stuck == []


def test_dead_cluster_interior_surfaced_not_safe(labeled):
    """The interior of a dead cluster is surfaced as likely_dead with
    cluster evidence — never safe standalone, since deleting it alone
    would break its (dead) caller."""
    _labels, by_id = labeled
    rec = by_id["sample_app.core:_legacy_transform"]
    assert rec.status.value == "likely_dead"
    assert any("unreachable" in e for e in rec.evidence_against_deletion)


def test_config_wired_handler_is_hedged_not_safe(labeled):
    """A string-wired handler is 'used'; the tool must not call it safe."""
    _labels, by_id = labeled
    rec = by_id["sample_app.dynamic:string_wired_task"]
    assert rec.status.value == "uncertain_dynamic_risk"
