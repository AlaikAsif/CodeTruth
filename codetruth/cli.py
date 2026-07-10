"""Human/script CLI.

    codetruth scan ./repo [--json out.json] [--status likely_dead] [--app-mode]
    codetruth scan ./repo --strict --min-rank 0.5 --group
    codetruth scan ./repo --ci                 # exit 1 if safe_to_delete found
    codetruth scan ./repo --html report.html
    codetruth check ./repo pkg.module:symbol
    codetruth plan ./repo pkg.module:symbol
    codetruth mcp
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .api import check_deletion_safety, plan_deletion, scan, scan_repos
from .core.models import Status
from .core.report import write_html_report

STATUS_ICON = {
    Status.SAFE_TO_DELETE: "[SAFE]  ",
    Status.LIKELY_DEAD: "[DEAD?] ",
    Status.UNCERTAIN_DYNAMIC_RISK: "[RISK]  ",
    Status.DEFINITELY_USED: "[USED]  ",
}


def _print_record(r, verbose: bool, indent: str = "") -> None:
    print(f"{indent}{STATUS_ICON[r.status]}{r.rank_score:.2f}  {r.symbol}  "
          f"({r.file}:{r.line})")
    if verbose:
        for e in r.evidence_for_deletion:
            print(f"{indent}    + {e}")
        for e in r.evidence_against_deletion:
            print(f"{indent}    - {e}")


def _cmd_scan(args) -> int:
    result = scan(args.repo, language=args.language,
                  treat_public_as_api=False if args.app_mode else None,
                  runtime_log=args.runtime_log, use_cache=not args.no_cache,
                  reachability="strict" if args.strict else "default")
    summary = result.summary()

    if args.json:
        result.save(args.json)
        print(f"Wrote full evidence to {args.json}")
    if args.html:
        write_html_report(result, args.html)
        print(f"Wrote HTML report to {args.html}")

    records = result.candidates()
    if args.status:
        records = [r for r in records if r.status.value == args.status]
    if args.min_rank is not None:
        records = [r for r in records if r.rank_score >= args.min_rank]

    shown = records[: args.limit]
    if args.group:
        by_file: dict[str, list] = defaultdict(list)
        for r in shown:
            by_file[r.file].append(r)
        for file in sorted(by_file):
            print(f"\n{file}")
            for r in by_file[file]:
                _print_record(r, args.verbose, indent="  ")
    else:
        for r in shown:
            _print_record(r, args.verbose)
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

    if args.ci:
        # Report-gate: fail the build when provably-dead code exists so a
        # human looks. Never deletes anything — advisory, as always.
        n = c["safe_to_delete"]
        if n:
            print(f"\nCI gate: {n} safe_to_delete symbol(s) found — failing "
                  "(review and remove, or mark as an entrypoint).",
                  file=sys.stderr)
            return 1
    return 0


def _cmd_check(args) -> int:
    response = check_deletion_safety(
        args.repo, args.symbol,
        treat_public_as_api=False if args.app_mode else None)
    print(json.dumps(response, indent=2))
    return 0


def _cmd_plan(args) -> int:
    response = plan_deletion(
        args.repo, args.symbol,
        treat_public_as_api=False if args.app_mode else None)
    print(json.dumps(response, indent=2))
    return 0


def _cmd_workspace(args) -> int:
    ws = scan_repos(args.repos, language=args.language,
                    treat_public_as_api=False if args.app_mode else None,
                    use_cache=not args.no_cache,
                    reachability="strict" if args.strict else "default")
    if args.json:
        Path(args.json).write_text(json.dumps(ws.to_dict(), indent=2),
                                   encoding="utf-8")
        print(f"Wrote workspace evidence to {args.json}")

    for c in ws.crossrefs[: args.limit]:
        print(f"[XREF]  {c.repo}:{c.symbol}  <-  {c.reason}")
    print()
    for label, result in ws.repos.items():
        c = result.summary()["status_counts"]
        print(f"{label}: safe {c['safe_to_delete']}  dead? "
              f"{c['likely_dead']}  uncertain {c['uncertain_dynamic_risk']}  "
              f"used {c['definitely_used']}")
    print(f"\n{len(ws.crossrefs)} cross-repo reference(s) found across "
          f"{len(ws.repos)} repos.")
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
    p_scan.add_argument("--language", "-l", default="python",
                        choices=["python", "javascript", "typescript"],
                        help="language plugin (javascript needs "
                             "`pip install codetruth[javascript]`)")
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
    p_scan.add_argument("--strict", action="store_true",
                        help="strict reachability: flag code not reachable "
                             "from any real entry point (detects internally-"
                             "connected but orphaned clumps)")
    p_scan.add_argument("--min-rank", type=float, default=None,
                        help="only show candidates with rank_score >= this "
                             "(0-1); trims the noisy tail of the review queue")
    p_scan.add_argument("--group", action="store_true",
                        help="group the output by file")
    p_scan.add_argument("--html", help="write a standalone HTML report here")
    p_scan.add_argument("--ci", action="store_true",
                        help="exit non-zero if any safe_to_delete code exists "
                             "(a dead-code report gate; never deletes)")
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

    p_ws = sub.add_parser(
        "workspace", help="scan multiple repos as one system; overlay "
                          "cross-service usage (routes, shared imports)")
    p_ws.add_argument("repos", nargs="+", help="two or more repo paths")
    p_ws.add_argument("--language", "-l", default="python",
                      choices=["python", "javascript", "typescript"])
    p_ws.add_argument("--app-mode", action="store_true")
    p_ws.add_argument("--strict", action="store_true")
    p_ws.add_argument("--no-cache", action="store_true")
    p_ws.add_argument("--limit", type=int, default=50)
    p_ws.add_argument("--json", help="write full workspace evidence JSON here")
    p_ws.set_defaults(func=_cmd_workspace)

    p_mcp = sub.add_parser("mcp", help="run the MCP server (stdio)")
    p_mcp.set_defaults(func=_cmd_mcp)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
