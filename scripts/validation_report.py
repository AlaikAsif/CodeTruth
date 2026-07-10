"""Scaled validation: automated false-positive audit + feature collection.

For each repo:
  - scan in library mode (conservative);
  - for every safe_to_delete verdict, independently verify (regex over the
    whole repo, outside the symbol's own file) that the name occurs nowhere
    else — any hit is a FALSE POSITIVE, the metric that matters;
  - dump a feature row per symbol for calibration.

Writes validation/REPORT.md (human summary) and validation/features.jsonl
(one JSON row per symbol across all repos).

Usage:
  python scripts/validation_report.py <repo> [<repo> ...]
Exit code = total false positives (0 = clean).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from codetruth import scan
from codetruth.languages.python.extractor import SKIP_DIRS

OUT_DIR = Path(__file__).resolve().parents[1] / "validation"


def _repo_text(root: Path) -> dict[str, str]:
    texts = {}
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            texts[path.relative_to(root).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            pass
    return texts


def audit_repo(root: Path, feat_out) -> dict:
    t0 = time.time()
    result = scan(root, use_cache=False)
    elapsed = time.time() - t0
    counts = result.summary()["status_counts"]

    # feature rows
    for r in result.records:
        feat_out.write(json.dumps({
            "repo": root.name, "symbol": r.symbol, "type": r.type.value,
            "status": r.status.value, "rank_score": r.rank_score,
            "inbound_strong": r.inbound_strong, "inbound_weak": r.inbound_weak,
            "exported": r.exported,
        }) + "\n")

    safe = [r for r in result.records if r.status.value == "safe_to_delete"]
    fps = []
    if safe:
        texts = _repo_text(root)
        for rec in safe:
            pattern = re.compile(rf"\b{re.escape(rec.name)}\b")
            for rel, text in texts.items():
                if rel == rec.file:
                    continue
                if pattern.search(text):
                    fps.append((rec.symbol, rel))
                    break

    return {
        "repo": root.name, "symbols": result.symbol_count,
        "edges": result.edge_count, "seconds": round(elapsed, 1),
        "counts": counts, "safe": len(safe), "false_positives": fps,
        "warnings": len(result.warnings),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    OUT_DIR.mkdir(exist_ok=True)
    rows = []
    with (OUT_DIR / "features.jsonl").open("w", encoding="utf-8") as feat_out:
        for arg in sys.argv[1:]:
            root = Path(arg).resolve()
            if not root.is_dir():
                print(f"skip {root}: not a directory")
                continue
            print(f"scanning {root.name} ...", flush=True)
            rows.append(audit_repo(root, feat_out))

    total_fp = sum(len(r["false_positives"]) for r in rows)
    total_safe = sum(r["safe"] for r in rows)
    total_sym = sum(r["symbols"] for r in rows)

    lines = ["# CodeTruth validation report", "",
             f"Generated {time.strftime('%Y-%m-%d')} by "
             "`scripts/validation_report.py`. Library mode "
             "(`treat_public_as_api=True`, conservative).", "",
             "## False-positive audit (the metric that matters)", "",
             f"**{total_fp} false positive(s)** across {len(rows)} repos, "
             f"{total_sym} symbols, {total_safe} `safe_to_delete` verdicts. "
             "A false positive = a symbol marked `safe_to_delete` whose name "
             "occurs elsewhere in the repo (i.e. possibly used).", "",
             "| repo | symbols | edges | scan (s) | safe | likely_dead | "
             "uncertain | used | FP |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for r in rows:
        c = r["counts"]
        lines.append(
            f"| {r['repo']} | {r['symbols']} | {r['edges']} | {r['seconds']} "
            f"| {r['safe']} | {c['likely_dead']} | "
            f"{c['uncertain_dynamic_risk']} | {c['definitely_used']} | "
            f"{len(r['false_positives'])} |")
    lines += ["", f"**Totals:** {total_sym} symbols, {total_safe} "
              f"safe_to_delete, **{total_fp} false positives.**", ""]

    if total_fp:
        lines += ["### False positives found", ""]
        for r in rows:
            for sym, where in r["false_positives"]:
                lines.append(f"- `{r['repo']}` {sym} — name also in {where}")
    else:
        lines += ["No false positives: every `safe_to_delete` verdict names a "
                  "symbol that appears nowhere else in its repository.", ""]

    (OUT_DIR / "REPORT.md").write_text("\n".join(lines) + "\n",
                                       encoding="utf-8")
    print(f"\nWrote {OUT_DIR / 'REPORT.md'} and features.jsonl")
    print(f"TOTAL: {total_sym} symbols, {total_safe} safe, {total_fp} FP")
    return min(total_fp, 125)


if __name__ == "__main__":
    sys.exit(main())
