"""Validation harness, false-negative side (PLAN.md §9).

The FP audit (validate_real_repos.py) proves the tool never says
safe_to_delete about used code. This script measures the *other* direction:
of the code that really is dead, how much does the tool actually surface as
deletable — and how much gets stuck in the review tiers?

Ground truth is a small hand-labeled table: symbol id -> "dead" | "used".
Provide it as a JSON file:

    {
      "repo": "path/to/repo",
      "treat_public_as_api": false,
      "labels": {
        "pkg.mod:func_a": "dead",
        "pkg.mod:Class.method": "used",
        ...
      }
    }

Usage:  python scripts/measure_recall.py labels1.json [labels2.json ...]

Reports, per repo and overall:
  - FALSE POSITIVES: labeled 'used' but ranked safe_to_delete   (must be 0)
  - recall@safe:     dead symbols the tool marked safe_to_delete
  - recall@queue:    dead symbols surfaced anywhere in the review queue
                     (safe_to_delete + likely_dead + uncertain)
  - stuck:           dead symbols the tool called definitely_used
                     (true false negatives — dead code it failed to flag)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from codetruth import scan


def audit(spec_path: Path) -> tuple[int, int, int, int, int]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    # Resolve the repo relative to the labels file so specs are portable.
    repo_spec = Path(spec["repo"])
    repo = (repo_spec if repo_spec.is_absolute()
            else spec_path.parent / repo_spec).resolve()
    labels: dict[str, str] = spec["labels"]
    result = scan(repo, treat_public_as_api=spec.get("treat_public_as_api", True))

    by_id = {r.symbol: r for r in result.records}
    fp = safe_dead = queue_dead = stuck_dead = n_dead = 0

    print(f"\n=== {repo}  ({len(labels)} labeled symbols)")
    for sym, label in labels.items():
        rec = by_id.get(sym)
        if rec is None:
            print(f"    ?  {sym}: not found in scan (check the id)")
            continue
        status = rec.status.value
        if label == "used":
            if status == "safe_to_delete":
                fp += 1
                print(f"    FALSE POSITIVE  {sym}: used, but ranked safe_to_delete")
        elif label == "dead":
            n_dead += 1
            if status == "safe_to_delete":
                safe_dead += 1
                queue_dead += 1
            elif status in ("likely_dead", "uncertain_dynamic_risk"):
                queue_dead += 1
            else:  # definitely_used
                stuck_dead += 1
                print(f"    STUCK  {sym}: dead, but ranked definitely_used "
                      f"(false negative)")

    if n_dead:
        print(f"    dead symbols: {n_dead} | safe_to_delete: {safe_dead} "
              f"({safe_dead / n_dead:.0%})  in-queue: {queue_dead} "
              f"({queue_dead / n_dead:.0%})  stuck: {stuck_dead}")
    print(f"    false positives: {fp}")
    return fp, safe_dead, queue_dead, stuck_dead, n_dead


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tot = [0, 0, 0, 0, 0]
    for arg in sys.argv[1:]:
        for i, v in enumerate(audit(Path(arg))):
            tot[i] += v
    fp, safe, queue, stuck, dead = tot
    print("\n=== OVERALL")
    print(f"    false positives (used ranked safe): {fp}")
    if dead:
        print(f"    recall@safe:  {safe}/{dead} ({safe / dead:.0%})")
        print(f"    recall@queue: {queue}/{dead} ({queue / dead:.0%})")
        print(f"    stuck (dead ranked used): {stuck}/{dead} ({stuck / dead:.0%})")
    return 1 if fp else 0


if __name__ == "__main__":
    sys.exit(main())
