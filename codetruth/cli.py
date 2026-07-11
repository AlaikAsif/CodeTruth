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


def _progress_for(args):
    """A ProgressRenderer when appropriate: forced by --progress, silenced by
    --no-progress, otherwise auto (only when stderr is a live terminal)."""
    from .core.progress import ProgressRenderer
    flag = getattr(args, "progress_mode", None)
    if flag == "off":
        return None
    if flag != "on" and not sys.stderr.isatty():
        return None
    return ProgressRenderer()


def _cmd_scan(args) -> int:
    renderer = _progress_for(args)
    try:
        result = scan(args.repo, language=args.language,
                      treat_public_as_api=False if args.app_mode else None,
                      runtime_log=args.runtime_log,
                      use_cache=not args.no_cache,
                      reachability="strict" if args.strict else "default",
                      progress=renderer)
    finally:
        if renderer is not None:
            renderer.close()
    summary = result.summary()

    if args.json:
        result.save(args.json)
        print(f"Wrote full evidence to {args.json}")
    if args.html:
        write_html_report(result, args.html)
        print(f"Wrote HTML report to {args.html}")
    if args.sarif:
        from .core.sarif import write_sarif
        write_sarif(result, args.sarif)
        print(f"Wrote SARIF to {args.sarif}")

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
        from .core.baseline import (default_path, diff_against_baseline,
                                    load_baseline)
        baseline_path = Path(args.baseline) if args.baseline \
            else default_path(args.repo)
        baseline = load_baseline(baseline_path)
        if baseline is not None:
            diff = diff_against_baseline(result, baseline)
            if diff.resolved:
                print(f"baseline: {len(diff.resolved)} accepted finding(s) "
                      f"resolved — refresh with `codetruth baseline` to shrink "
                      "the baseline.")
            if diff.new_safe:
                print(f"\nCI gate: {len(diff.new_safe)} NEW safe_to_delete "
                      f"symbol(s) not in the baseline ({baseline_path.name}):",
                      file=sys.stderr)
                for r in diff.new_safe:
                    print(f"  {r.symbol}  ({r.file}:{r.line})", file=sys.stderr)
                print("Review and remove them, mark them as entrypoints, or "
                      "re-accept with `codetruth baseline`.", file=sys.stderr)
                return 1
            print(f"CI gate: no new dead code beyond the baseline "
                  f"({len(baseline.get('findings', {}))} accepted).")
            return 0
        n = c["safe_to_delete"]
        if n:
            print(f"\nCI gate: {n} safe_to_delete symbol(s) found — failing "
                  "(review and remove, mark as an entrypoint, or accept the "
                  "current state with `codetruth baseline`).",
                  file=sys.stderr)
            return 1
    return 0


def _cmd_baseline(args) -> int:
    from .core.baseline import default_path, write_baseline
    renderer = _progress_for(args)
    try:
        result = scan(args.repo, language=args.language,
                      treat_public_as_api=False if args.app_mode else None,
                      use_cache=not args.no_cache,
                      reachability="strict" if args.strict else "default",
                      progress=renderer)
    finally:
        if renderer is not None:
            renderer.close()
    path = Path(args.output) if args.output else default_path(args.repo)
    doc = write_baseline(result, path)
    n = len(doc["findings"])
    print(f"Baseline written: {path} ({n} accepted finding(s)).")
    print("From now on `codetruth scan --ci` fails only on newly introduced "
          "provably-dead code. Commit the baseline file.")
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


def _cmd_report_fp(args) -> int:
    """Generate a prefilled GitHub issue for a verdict the user disputes.
    FP reports are the project's lifeblood — make filing one a single step."""
    import platform
    import urllib.parse

    from . import __version__

    response = check_deletion_safety(
        args.repo, args.symbol,
        treat_public_as_api=False if args.app_mode else None)
    if not response.get("found"):
        print(f"Symbol not found: {args.symbol}", file=sys.stderr)
        return 1
    rec = response.get("record") or (response.get("candidates") or [{}])[0]

    body = "\n".join([
        "## Disputed verdict",
        f"- **Symbol:** `{rec.get('symbol')}`  ({rec.get('file')}:{rec.get('line')})",
        f"- **Verdict:** `{rec.get('status')}` (rank {rec.get('rank_score')})",
        f"- **codetruth:** {__version__}  ·  **python:** "
        f"{platform.python_version()}  ·  **os:** {platform.system()}",
        "",
        "## Why I believe this is wrong",
        "<!-- how is this symbol actually used? (framework, config, another",
        "     repo, runtime reflection, ...) -->",
        "",
        "## Evidence the tool reported",
        "```json",
        json.dumps({k: rec.get(k) for k in ("evidence_for_deletion",
                                            "evidence_against_deletion",
                                            "inbound_strong", "inbound_weak")},
                   indent=2),
        "```",
    ])
    title = f"False positive: {rec.get('symbol')} ranked {rec.get('status')}"
    url = ("https://github.com/AlaikAsif/CodeTruth/issues/new?"
           + urllib.parse.urlencode({"title": title, "body": body,
                                     "labels": "false-positive"}))
    print(body)
    print("\n--- open a prefilled issue ---")
    print(url if len(url) < 7500 else
          "https://github.com/AlaikAsif/CodeTruth/issues/new "
          "(body too long for a URL — paste the text above)")
    return 0


def _cmd_mcp(_args) -> int:
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        print(
            "The MCP server needs the optional 'mcp' extra, which isn't "
            "installed.\n\n"
            "    pip install \"codetruth[mcp]\"\n\n"
            "Then register it:  claude mcp add codetruth -- codetruth mcp\n"
            "(The CLI and Python API work without it — the extra only adds "
            "the agent-facing MCP server.)",
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
    p_scan.add_argument("--sarif", help="write SARIF 2.1.0 here (GitHub Code "
                                        "Scanning shows findings as inline "
                                        "PR annotations)")
    p_scan.add_argument("--ci", action="store_true",
                        help="exit non-zero on provably-dead code (a report "
                             "gate; never deletes). With a baseline file, "
                             "fails only on NEW findings.")
    p_scan.add_argument("--baseline", default=None,
                        help="baseline file for --ci (default: "
                             "<repo>/.codetruth.baseline.json when present)")
    p_scan.add_argument("--progress", dest="progress_mode",
                        action="store_const", const="on",
                        help="force the live progress line even when stderr "
                             "is not a terminal")
    p_scan.add_argument("--no-progress", dest="progress_mode",
                        action="store_const", const="off",
                        help="never show the progress line")
    p_scan.set_defaults(func=_cmd_scan, progress_mode=None)

    p_base = sub.add_parser(
        "baseline", help="accept all current findings so --ci fails only on "
                         "newly introduced dead code")
    p_base.add_argument("repo")
    p_base.add_argument("--language", "-l", default="python",
                        choices=["python", "javascript", "typescript"])
    p_base.add_argument("--app-mode", action="store_true")
    p_base.add_argument("--strict", action="store_true")
    p_base.add_argument("--no-cache", action="store_true")
    p_base.add_argument("--output", "-o", default=None,
                        help="where to write (default: "
                             "<repo>/.codetruth.baseline.json)")
    p_base.add_argument("--progress", dest="progress_mode",
                        action="store_const", const="on")
    p_base.add_argument("--no-progress", dest="progress_mode",
                        action="store_const", const="off")
    p_base.set_defaults(func=_cmd_baseline, progress_mode=None)

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

    p_fp = sub.add_parser(
        "report-fp", help="generate a prefilled GitHub issue for a verdict "
                          "you believe is wrong")
    p_fp.add_argument("repo")
    p_fp.add_argument("symbol")
    p_fp.add_argument("--app-mode", action="store_true")
    p_fp.set_defaults(func=_cmd_report_fp)

    p_mcp = sub.add_parser("mcp", help="run the MCP server (stdio)")
    p_mcp.set_defaults(func=_cmd_mcp)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ncancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
