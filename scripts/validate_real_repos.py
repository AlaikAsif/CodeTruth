"""Validation harness (PLAN.md §9): run CodeTruth against real repositories
and independently audit the verdict that matters — safe_to_delete.

For every safe_to_delete record, this script re-checks with its own regex
sweep (independent of the scanner's internal verifier) that the symbol's
name appears nowhere in the repo outside its definition file. Any hit is a
FALSE POSITIVE — the failure mode that destroys trust in the tool.

Usage:  python scripts/validate_real_repos.py <repo_path> [<repo_path> ...]
Exit code: number of false positives found (0 = clean).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from codetruth import scan
from codetruth.languages.python.extractor import SKIP_DIRS


def repo_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in {".py", ".yaml", ".yml", ".json", ".toml",
                                   ".ini", ".cfg", ".txt", ".md", ".rst"}:
            yield path


def audit_repo(root: Path) -> int:
    t0 = time.time()
    result = scan(root)
    elapsed = time.time() - t0
    counts = result.summary()["status_counts"]
    print(f"\n=== {root}")
    print(f"    {result.symbol_count} symbols, {result.edge_count} edges, "
          f"{elapsed:.1f}s")
    print(f"    safe_to_delete: {counts['safe_to_delete']}  "
          f"likely_dead: {counts['likely_dead']}  "
          f"uncertain: {counts['uncertain_dynamic_risk']}  "
          f"used: {counts['definitely_used']}")

    safe = [r for r in result.records if r.status.value == "safe_to_delete"]
    if not safe:
        print("    audit: no safe_to_delete verdicts to check")
        return 0

    texts = {}
    for path in repo_text_files(root):
        try:
            texts[path.relative_to(root).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            pass

    violations = 0
    for rec in safe:
        pattern = re.compile(rf"\b{re.escape(rec.name)}\b")
        for rel, text in texts.items():
            if rel == rec.file:
                continue  # scanner already verified the home file by span
            if pattern.search(text):
                violations += 1
                print(f"    FALSE POSITIVE: {rec.symbol} "
                      f"({rec.file}:{rec.line}) — name also occurs in {rel}")
                break
    print(f"    audit: {len(safe)} safe_to_delete verdict(s), "
          f"{violations} false positive(s)")
    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    total = 0
    for arg in sys.argv[1:]:
        root = Path(arg).resolve()
        if not root.is_dir():
            print(f"skipping {root}: not a directory")
            continue
        total += audit_repo(root)
    print(f"\nTOTAL false positives: {total}")
    return min(total, 125)


if __name__ == "__main__":
    sys.exit(main())
