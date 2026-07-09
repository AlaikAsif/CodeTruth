"""The headline interface: CodeTruth as an MCP server.

Agent workflow:
  1. Agent identifies a symbol it wants to delete.
  2. Agent calls check_deletion_safety(repo_path, symbol).
  3. CodeTruth returns the evidence record.
  4. Agent only deletes on status == "safe_to_delete"; anything else routes
     to human review or is left alone.

Register with Claude Code:
  claude mcp add codetruth -- codetruth mcp
"""
from __future__ import annotations

import time
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .api import check_deletion_safety as _check
from .api import plan_deletion as _plan
from .api import scan as _scan
from .core.scanner import ScanResult

mcp = FastMCP(
    "codetruth",
    instructions=(
        "Deletion-safety verification for code symbols. Before deleting any "
        "symbol, call check_deletion_safety and only proceed when status is "
        "'safe_to_delete'. 'likely_dead' and 'uncertain_dynamic_risk' require "
        "human review. Detection is deterministic — do not override it with "
        "your own judgment about whether code looks unused."),
)

_CACHE: dict[tuple, tuple[float, ScanResult]] = {}
_CACHE_TTL_SECONDS = 300.0


def _cached_scan(repo_path: str, treat_public_as_api: Optional[bool],
                 force_rescan: bool = False,
                 reachability: str = "default",
                 language: str = "python") -> ScanResult:
    key = (repo_path, treat_public_as_api, reachability, language)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and not force_rescan and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    # force_rescan also busts the persistent on-disk cache, not just the
    # in-memory TTL cache.
    result = _scan(repo_path, language=language,
                   treat_public_as_api=treat_public_as_api,
                   use_cache=not force_rescan, reachability=reachability)
    _CACHE[key] = (now, result)
    return result


@mcp.tool()
def scan(repo_path: str, status: str = "", limit: int = 100,
         treat_public_as_api: Optional[bool] = None,
         force_rescan: bool = False, strict: bool = False,
         language: str = "python") -> dict:
    """Scan a repository and return deletion-safety evidence records.

    Args:
        repo_path: absolute path to the repository root.
        status: optional filter — one of safe_to_delete, likely_dead,
            uncertain_dynamic_risk, definitely_used. Empty = all candidates
            (everything not proven used).
        limit: max records returned.
        treat_public_as_api: True for libraries (conservative). False only
            for application repos nothing external imports. None (default)
            defers to app_mode in the repo's .codetruth.toml, else True.
        force_rescan: bypass the 5-minute scan cache.
        strict: strict reachability — flag code that is internally connected
            but not reachable from any real entry point (route, CLI command,
            __main__, test, declared entrypoint). Orphaned clumps come back
            grouped via each record's 'cluster' field.
        language: 'python' (default) or 'javascript'/'typescript' (beta,
            requires the codetruth[javascript] extra).
    """
    result = _cached_scan(repo_path, treat_public_as_api, force_rescan,
                          "strict" if strict else "default", language)
    if status:
        records = [r for r in result.records if r.status.value == status]
    else:
        records = result.candidates()
    return {
        "summary": result.summary(),
        "records": [r.to_dict() for r in records[:limit]],
        "truncated": len(records) > limit,
        "note": "Only 'safe_to_delete' may be deleted without human review.",
    }


@mcp.tool()
def check_deletion_safety(repo_path: str, symbol: str,
                          treat_public_as_api: Optional[bool] = None,
                          force_rescan: bool = False) -> dict:
    """Check whether one symbol is safe to delete. Call this BEFORE deleting.

    Args:
        repo_path: absolute path to the repository root.
        symbol: symbol id ('pkg.module:Qual.name'), dotted path, or bare name.
        treat_public_as_api: True for libraries (default, conservative).
        force_rescan: bypass the 5-minute scan cache.

    Returns the evidence record: status, risk_level, recommended_action,
    and the evidence for/against deletion. Only delete on 'safe_to_delete'.
    """
    result = _cached_scan(repo_path, treat_public_as_api, force_rescan)
    return _check(repo_path, symbol, result=result)


@mcp.tool()
def plan_deletion(repo_path: str, symbol: str,
                  treat_public_as_api: Optional[bool] = None,
                  force_rescan: bool = False) -> dict:
    """Advisory plan describing what removing a symbol would involve: the
    exact source span, imports that would become orphaned, and any __all__
    entry. CodeTruth NEVER applies the plan — it is information for the
    human/agent who decides. Only act when status is 'safe_to_delete'.

    Args:
        repo_path: absolute path to the repository root.
        symbol: symbol id ('pkg.module:Qual.name'), dotted path, or bare name.
        treat_public_as_api: True for libraries (default, conservative).
        force_rescan: bypass the scan caches.
    """
    result = _cached_scan(repo_path, treat_public_as_api, force_rescan)
    return _plan(repo_path, symbol, result=result)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
