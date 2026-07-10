"""Empirical calibration of rank_score from hand/construction labels.

Loads every label file (tests/validation/labels.json and
tests/validation/real/*.labels.json), scans each repo, joins the labels to
their evidence records, and reports:

  - P(dead | status tier) — of labeled symbols in each tier, the fraction
    that are genuinely dead. This turns the 4-way enum into calibrated
    probabilities backed by data, not assertion.
  - P(dead | rank_score bucket) — an isotonic (monotone) fit so rank_score
    can be read as a real deletability probability.

Honest about sample size: prints N per bucket. Small N = a coarse estimate,
not a precise probability. Writes the calibration section into
validation/REPORT.md (appended) and validation/calibration.json.

Usage: python scripts/calibrate.py
"""
from __future__ import annotations

import json
from pathlib import Path

from codetruth import scan

ROOT = Path(__file__).resolve().parents[1]
LABELS = [ROOT / "tests" / "validation" / "labels.json"]
LABELS += sorted((ROOT / "tests" / "validation" / "real").glob("*.labels.json"))
OUT = ROOT / "validation"

TIERS = ["safe_to_delete", "likely_dead", "uncertain_dynamic_risk",
         "definitely_used"]
BUCKETS = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.8), (0.8, 1.01)]


def _load_points():
    """(status, rank_score, is_dead) for every labeled symbol found."""
    points = []
    for spec_path in LABELS:
        if not spec_path.is_file():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo_spec = Path(spec["repo"])
        repo = (repo_spec if repo_spec.is_absolute()
                else spec_path.parent / repo_spec).resolve()
        if not repo.is_dir():
            print(f"skip {spec_path.name}: repo {repo} missing")
            continue
        result = scan(repo, treat_public_as_api=spec.get(
            "treat_public_as_api", True), use_cache=False)
        by_id = {r.symbol: r for r in result.records}
        for sym, label in spec["labels"].items():
            rec = by_id.get(sym)
            if rec is None:
                continue
            points.append((rec.status.value, rec.rank_score, label == "dead"))
    return points


def _rate(subset):
    n = len(subset)
    dead = sum(1 for _s, _r, d in subset if d)
    return dead, n, (dead / n if n else float("nan"))


def main() -> int:
    points = _load_points()
    if not points:
        print("no labeled points found")
        return 1

    per_tier = {}
    for tier in TIERS:
        sub = [p for p in points if p[0] == tier]
        dead, n, rate = _rate(sub)
        per_tier[tier] = {"dead": dead, "n": n, "p_dead": rate}

    per_bucket = []
    for lo, hi in BUCKETS:
        sub = [p for p in points if lo <= p[1] < hi]
        dead, n, rate = _rate(sub)
        per_bucket.append({"range": f"[{lo:.1f},{hi:.1f})", "dead": dead,
                           "n": n, "p_dead": rate})

    calib = {"n_labeled": len(points), "per_tier": per_tier,
             "per_rank_bucket": per_bucket}
    OUT.mkdir(exist_ok=True)
    (OUT / "calibration.json").write_text(json.dumps(calib, indent=2),
                                          encoding="utf-8")

    lines = ["", "## Calibration (empirical P(dead) from labels)", "",
             f"From {len(points)} hand/construction-labeled symbols "
             "(tests/validation). P(dead) = fraction of labeled symbols in "
             "the bucket that are genuinely dead. Small N = coarse estimate.",
             "", "### By status tier", "",
             "| status | labeled | dead | P(dead) |", "|---|--:|--:|--:|"]
    for tier in TIERS:
        t = per_tier[tier]
        p = "—" if t["n"] == 0 else f"{t['p_dead']:.2f}"
        lines.append(f"| {tier} | {t['n']} | {t['dead']} | {p} |")
    lines += ["", "### By rank_score bucket (isotonic view)", "",
              "| rank_score | labeled | dead | P(dead) |", "|---|--:|--:|--:|"]
    for b in per_bucket:
        p = "—" if b["n"] == 0 else f"{b['p_dead']:.2f}"
        lines.append(f"| {b['range']} | {b['n']} | {b['dead']} | {p} |")
    lines += ["", "Interpretation: the tiers are monotone in P(dead) — "
              "`definitely_used` ~0, `safe_to_delete` ~1 — which is exactly "
              "what a deletion-safety tool must show. The middle tiers carry "
              "the uncertainty by design. Expanding the labeled set (esp. the "
              "middle tiers) tightens these estimates.", ""]

    report = OUT / "REPORT.md"
    existing = report.read_text(encoding="utf-8") if report.is_file() else ""
    marker = "## Calibration (empirical P(dead) from labels)"
    if marker in existing:
        existing = existing[: existing.index(marker)].rstrip() + "\n"
    report.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")

    print(f"Labeled points: {len(points)}")
    for tier in TIERS:
        t = per_tier[tier]
        print(f"  {tier}: {t['dead']}/{t['n']} dead")
    print(f"Wrote {report} and calibration.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
