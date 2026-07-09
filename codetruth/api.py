"""Programmatic API — what the MCP server and scripts call.

    from codetruth import scan, check_deletion_safety, plan_deletion
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .core.deletion import build_deletion_plan
from .core.plugin import get_plugin
from .core.scanner import ScanResult, scan_repo


def scan(repo_path: str | Path, language: str = "python",
         treat_public_as_api: Optional[bool] = None,
         runtime_log: Optional[str | Path] = None,
         use_cache: bool = True,
         reachability: str = "default") -> ScanResult:
    """Run all four layers over a repository and return the evidence set.

    treat_public_as_api: True (conservative, the default when neither the
    caller nor .codetruth.toml says otherwise) caps unreferenced public
    symbols at `likely_dead` because a library's consumers are invisible.
    False for application code; None defers to `app_mode` in .codetruth.toml.

    reachability: "default", or "strict" to detect useless clumps — code
    that is internally connected but never reached from any real entry point
    (route, CLI command, __main__, test, declared entrypoint). Strict-mode
    findings surface in the review queue with cluster grouping.

    use_cache=True reuses a persisted result (<repo>/.codetruth/index.json)
    when no source or config file has changed since the last scan.
    """
    return scan_repo(repo_path, language=language,
                     treat_public_as_api=treat_public_as_api,
                     runtime_log=runtime_log, use_cache=use_cache,
                     reachability=reachability)


def check_deletion_safety(repo_path: str | Path, symbol: str,
                          result: Optional[ScanResult] = None,
                          **scan_kwargs) -> dict:
    """The agent-facing question: 'may I delete this symbol?'

    Returns the evidence record(s) for the symbol, or candidate matches if
    the name is ambiguous. The agent should only act on `safe_to_delete`.
    """
    if result is None:
        result = scan(repo_path, **scan_kwargs)
    matches = result.find(symbol)
    if not matches:
        return {
            "found": False, "symbol": symbol,
            "message": "Symbol not found in the scanned repository. "
                       "Do NOT delete based on this response — verify the "
                       "symbol id (format: 'pkg.module:Qual.name').",
        }
    if len(matches) > 1:
        return {
            "found": True, "ambiguous": True, "symbol": symbol,
            "message": f"{len(matches)} symbols match — re-query with an exact id.",
            "candidates": [m.to_dict() for m in matches[:20]],
        }
    record = matches[0]
    return {"found": True, "ambiguous": False, "record": record.to_dict()}


def plan_deletion(repo_path: str | Path, symbol: str,
                  language: str = "python",
                  result: Optional[ScanResult] = None,
                  **scan_kwargs) -> dict:
    """Advisory plan for removing one symbol: exact span, orphaned imports,
    __all__ entry. Works for any status — the response carries the verdict so
    the reader knows whether acting on the plan is advised. CodeTruth never
    applies the plan.
    """
    safety = check_deletion_safety(repo_path, symbol, result=result,
                                   **scan_kwargs)
    if not safety.get("found") or safety.get("ambiguous"):
        return safety

    record = safety["record"]
    if record.get("deletion_plan"):
        plan = record["deletion_plan"]
    else:
        repo = Path(repo_path).resolve()
        plugin = get_plugin(language)
        modules, _warnings = plugin.extract(repo)
        sym = next((s for m in modules for s in m.symbols
                    if s.id == record["symbol"]), None)
        plan = build_deletion_plan(repo, sym) if sym is not None else None

    return {
        "found": True, "symbol": record["symbol"],
        "status": record["status"],
        "recommended_action": record["recommended_action"],
        "plan": plan,
        "warning": None if record["status"] == "safe_to_delete" else
                   f"status is {record['status']} — this plan is informational; "
                   "review is required before anyone acts on it",
    }
