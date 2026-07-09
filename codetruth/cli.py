"""Human/script CLI.

    codetruth scan ./repo [--json out.json] [--status likely_dead] [--app-mode]
    codetruth check ./repo pkg.module:symbol
    codetruth mcp
"""
from __future__ import annotations

import argparse
import json
import sys

from .api import check_deletion_safety, plan_deletion, scan
from .core.models import Status

STATUS_ICON = {
    Status.SAFE_TO_DELETE: "[SAFE]  ",
    Status.LIKELY_DEAD: "[DEAD?] ",
    Status.UNCERTAIN_DYNAMIC_RISK: "[RISK]  ",
    Status.DEFINITELY_USED: "[USED]  ",
}


def _cmd_scan(args) -> int:
    result = scan(args.repo, treat_public_as_api=not args.app_mode,
                  runtime_log=args.runtime_log, use_cache=not args.no_cache)
    summary = result.summary()

    if args.json:
        result.save(args.json)
        print(f"Wrote full evidence to {args.json}")

    records = result.candidates()
    if args.status:
        records = [r for r in records if r.status.value == args.status]

    for r in records[: args.limit]:
        print(f"{STATUS_ICON[r.status]}{r.rank_score:.2f}  {r.symbol}  "
              f"({r.file}:{r.line})")
        if args.verbose:
            for e in r.evidence_for_deletion:
                print(f"    + {e}")
            for e in r.evidence_against_deletion:
                print(f"    - {e}")
    if len(records) > args.limit:
        print(f"... {len(records) - args.limit} more (raise --limit or use --json)")

    c = summary["status_counts"]
    print(f"\n{summary['symbols']} symbols, {summary['edges']} edges | "
          f"safe_to_delete: {c['safe_to_delete']}  "
          f"likely_dead: {c['likely_dead']}  "
          f"uncertain: {c['uncertain_dynamic_risk']}  "
          f"used: {c['definitely_used']}")
    if result.warnings:
        print(f"warnings: {len(result.warnings)} (parse failures)")
    return 0


def _cmd_check(args) -> int:
    response = check_deletion_safety(args.repo, args.symbol,
                                     treat_public_as_api=not args.app_mode)
    print(json.dumps(response, indent=2))
    return 0


def _cmd_plan(args) -> int:
    response = plan_deletion(args.repo, args.symbol,
                             treat_public_as_api=not args.app_mode)
    print(json.dumps(response, indent=2))
    return 0


def _cmd_mcp(_args) -> int:
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        print("The MCP server requires the 'mcp' package: pip install codetruth[mcp]",
              file=sys.stderr)
        return 1
    mcp_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codetruth",
        description="Deletion-safety evidence for code symbols. "
                    "A risk assessor, not an oracle: only act on safe_to_delete.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="scan a repository")
    p_scan.add_argument("repo")
    p_scan.add_argument("--json", help="write full evidence JSON to this file")
    p_scan.add_argument("--status", choices=[s.value for s in Status])
    p_scan.add_argument("--limit", type=int, default=50)
    p_scan.add_argument("--verbose", "-v", action="store_true",
                        help="print evidence lines per symbol")
    p_scan.add_argument("--app-mode", action="store_true",
                        help="treat public symbols as internal (application, "
                             "not library) — allows safe_to_delete on them")
    p_scan.add_argument("--runtime-log", help="path to a runtime.jsonl trace")
    p_scan.add_argument("--no-cache", action="store_true",
                        help="ignore the persisted .codetruth/index.json result")
    p_scan.set_defaults(func=_cmd_scan)

    p_check = sub.add_parser("check", help="check one symbol's deletion safety")
    p_check.add_argument("repo")
    p_check.add_argument("symbol", help="e.g. pkg.module:function_name")
    p_check.add_argument("--app-mode", action="store_true")
    p_check.set_defaults(func=_cmd_check)

    p_plan = sub.add_parser(
        "plan", help="advisory deletion plan for one symbol (never applied)")
    p_plan.add_argument("repo")
    p_plan.add_argument("symbol", help="e.g. pkg.module:function_name")
    p_plan.add_argument("--app-mode", action="store_true")
    p_plan.set_defaults(func=_cmd_plan)

    p_mcp = sub.add_parser("mcp", help="run the MCP server (stdio)")
    p_mcp.set_defaults(func=_cmd_mcp)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
