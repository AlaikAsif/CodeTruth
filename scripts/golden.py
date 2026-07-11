"""Golden-corpus freeze/diff: differential safety net for engine changes.

Any precision work must be provably non-regressive on the metric that
matters. `freeze` snapshots every verdict for the fixture repos plus the
real-package corpus; `diff` rescans and reports every status transition,
flagging the dangerous direction (anything newly safe_to_delete) for the
independent textual audit.

    python scripts/golden.py freeze
    python scripts/golden.py diff

Golden files live in validation/golden/ (gitignored — they embed
machine-local site-packages results; regenerate per machine).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from codetruth import scan

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "validation" / "golden"
SP = Path(r"C:\ProgramData\anaconda3\Lib\site-packages")

CORPUS: dict[str, dict] = {
    # fixtures (library mode unless noted)
    "fx-plain": {"repo": ROOT / "tests/fixtures/plain_repo"},
    "fx-fastapi": {"repo": ROOT / "tests/fixtures/fastapi_repo"},
    "fx-django": {"repo": ROOT / "tests/fixtures/django_repo"},
    "fx-app": {"repo": ROOT / "tests/validation/app",
               "treat_public_as_api": False},
    # real packages
    **{name: {"repo": SP / name}
       for name in ("requests", "flask", "click", "jinja2", "werkzeug",
                    "rich", "pydantic", "urllib3", "sqlalchemy", "networkx")},
}

SAFE = "safe_to_delete"


def _scan(spec: dict):
    return scan(spec["repo"],
                treat_public_as_api=spec.get("treat_public_as_api"),
                use_cache=False)


def freeze() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for label, spec in CORPUS.items():
        if not Path(spec["repo"]).is_dir():
            print(f"skip {label}: missing")
            continue
        result = _scan(spec)
        doc = {r.symbol: r.status.value for r in result.records}
        (GOLDEN_DIR / f"{label}.json").write_text(
            json.dumps(doc, sort_keys=True), encoding="utf-8")
        c = result.summary()["status_counts"]
        print(f"froze {label}: {len(doc)} symbols  "
              f"(safe={c[SAFE]} dead?={c['likely_dead']} "
              f"unc={c['uncertain_dynamic_risk']} used={c['definitely_used']})")
    return 0


def _audit_new_safe(repo: Path, names: list[str], files: dict) -> list[str]:
    """Independent textual check on newly-safe symbols: the name must occur
    nowhere outside its defining file. Returns violations."""
    bad = []
    for sym in names:
        name = sym.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
        home = files.get(sym, "")
        pat = re.compile(rf"\b{re.escape(name)}\b")
        for py in repo.rglob("*.py"):
            rel = py.relative_to(repo).as_posix()
            if rel == home or "__pycache__" in rel:
                continue
            try:
                if pat.search(py.read_text(encoding="utf-8", errors="replace")):
                    bad.append(f"{sym} (name also in {rel})")
                    break
            except OSError:
                pass
    return bad


def diff() -> int:
    total_trans: Counter = Counter()
    violations: list[str] = []
    for label, spec in CORPUS.items():
        gpath = GOLDEN_DIR / f"{label}.json"
        if not gpath.is_file() or not Path(spec["repo"]).is_dir():
            continue
        golden = json.loads(gpath.read_text(encoding="utf-8"))
        result = _scan(spec)
        now = {r.symbol: r.status.value for r in result.records}
        files = {r.symbol: r.file for r in result.records}

        trans: Counter = Counter()
        new_safe = []
        for sym, old in golden.items():
            new = now.get(sym, "<gone>")
            if new != old:
                trans[(old, new)] += 1
                if new == SAFE:
                    new_safe.append(sym)
        for sym in now:
            if sym not in golden:
                trans[("<new>", now[sym])] += 1
                if now[sym] == SAFE:
                    new_safe.append(sym)

        if trans:
            print(f"\n{label}:")
            for (old, new), n in trans.most_common():
                print(f"  {n:6d}  {old} -> {new}")
        if new_safe:
            bad = _audit_new_safe(Path(spec["repo"]), new_safe, files)
            print(f"  newly safe: {len(new_safe)} — textual audit: "
                  f"{len(bad)} violation(s)")
            violations += [f"{label}: {b}" for b in bad]
        total_trans.update(trans)

    print("\n=== TOTAL transitions ===")
    for (old, new), n in total_trans.most_common():
        print(f"  {n:6d}  {old} -> {new}")
    if violations:
        print("\nFALSE-POSITIVE VIOLATIONS (newly safe but name found elsewhere):")
        for v in violations:
            print("  " + v)
        return 1
    print("\nNo textual-audit violations among newly-safe symbols.")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "freeze":
        raise SystemExit(freeze())
    if mode == "diff":
        raise SystemExit(diff())
    print(__doc__)
    raise SystemExit(2)
