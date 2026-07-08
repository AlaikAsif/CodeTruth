"""Programmatic API — what the MCP server and scripts call.

    from codetruth import scan, check_deletion_safety
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .core.scanner import ScanResult, scan_repo


def scan(repo_path: str | Path, language: str = "python",
         treat_public_as_api: bool = True,
         runtime_log: Optional[str | Path] = None) -> ScanResult:
    """Run all four layers over a repository and return the evidence set.

    treat_public_as_api=True (default, conservative) caps unreferenced public
    symbols at `likely_dead` because a library's consumers are invisible.
    Set it False for application code that nothing external imports.
    """
    return scan_repo(repo_path, language=language,
                     treat_public_as_api=treat_public_as_api,
                     runtime_log=runtime_log)


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
