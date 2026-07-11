"""Phase-5 performance: a persistent scan cache keyed by file fingerprints.

A scan is deterministic given the repo's source + config bytes, so we can
skip it entirely when nothing has changed since the last run. The cache lives
at <repo>/.codetruth/index.json and survives process restarts — which the
MCP server's in-memory TTL cache does not.

Invalidation is whole-repo and mtime+size based: if any tracked file's
(mtime_ns, size) differs from what produced the cached result, the cache
misses and a full rescan runs. We deliberately do NOT patch the graph
incrementally per changed file — edges cross files, and a stale edge could
turn a used symbol into a false safe_to_delete, the one error class the tool
exists to prevent. Correctness first; the cache only ever short-circuits an
exact, verified match.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .models import EvidenceRecord

if TYPE_CHECKING:
    from .scanner import ScanResult

# Bump on ANY change to classification, ranking, or the record schema — a
# cached result from older logic must never be served by newer code.
CACHE_VERSION = 6  # 6: enum-member caution + bare pydantic validators (0.6.1)
CACHE_DIRNAME = ".codetruth"
CACHE_FILENAME = "index.json"


def _cache_path(repo: Path) -> Path:
    return repo / CACHE_DIRNAME / CACHE_FILENAME


def fingerprint(repo: Path, source_files: list[Path]) -> dict[str, list[int]]:
    """{rel_path: [mtime_ns, size]} for every file whose bytes affect a scan."""
    fp: dict[str, list[int]] = {}
    for path in source_files:
        try:
            st = path.stat()
        except OSError:
            continue
        rel = path.relative_to(repo).as_posix()
        fp[rel] = [st.st_mtime_ns, st.st_size]
    return fp


def load(repo: Path, language: str, treat_public_as_api: bool,
         current_fp: dict[str, list[int]],
         reachability: str = "default") -> Optional["ScanResult"]:
    """Return a cached ScanResult iff it matches the current inputs exactly."""
    from .scanner import ScanResult  # local import: avoid a cycle

    path = _cache_path(repo)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if (doc.get("version") != CACHE_VERSION
            or doc.get("language") != language
            or doc.get("treat_public_as_api") != treat_public_as_api
            or doc.get("reachability", "default") != reachability
            or doc.get("fingerprint") != current_fp):
        return None

    try:
        records = [EvidenceRecord.from_dict(r) for r in doc["records"]]
        return ScanResult(
            repo_path=str(repo), language=language, records=records,
            symbol_count=doc["symbol_count"], edge_count=doc["edge_count"],
            warnings=list(doc.get("warnings", [])),
        )
    except (KeyError, ValueError):
        return None  # schema drift — treat as a miss and rescan


def save(repo: Path, result: "ScanResult", language: str,
         treat_public_as_api: bool, current_fp: dict[str, list[int]],
         reachability: str = "default") -> None:
    doc = {
        "version": CACHE_VERSION,
        "language": language,
        "treat_public_as_api": treat_public_as_api,
        "reachability": reachability,
        "fingerprint": current_fp,
        "symbol_count": result.symbol_count,
        "edge_count": result.edge_count,
        "warnings": result.warnings,
        "records": [r.to_dict() for r in result.records],
    }
    path = _cache_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX and Windows
    except OSError:
        pass  # a read-only repo just means no persistent cache; not fatal
