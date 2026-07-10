# CodeTruth

[![CI](https://github.com/AlaikAsif/CodeTruth/actions/workflows/ci.yml/badge.svg)](https://github.com/AlaikAsif/CodeTruth/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/codetruth.svg)](https://pypi.org/project/codetruth/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**A verification layer that lets AI agents safely delete code in large codebases.**

Agents hallucinate absence of usage. CodeTruth inverts the question — instead of
*"is this code used?"* it asks **"can we prove this code is used?"** — and only
surfaces a symbol for deletion when it fails to find *any* usage path: no call,
no import, no inheritance, no string reference, no reflection target, no
framework registration. Detection is deterministic; the agent only reads the
evidence and decides. It is a **risk assessor for code deletion**, not a dead
code detector.

## Statuses

| Status | Meaning | Recommended action |
|---|---|---|
| `safe_to_delete` | zero usage paths found under every analysis rule, **and** the name verified absent from all repo text outside its own definition | `delete` |
| `likely_dead` | no usage found, but external exposure can't be ruled out (public API, module, test-only) | `review_required` |
| `uncertain_dynamic_risk` | weak evidence exists (string refs, reflection, dynamic module) | `review_required` |
| `definitely_used` | strong reference or framework entry point proven | `keep` |

## Install

Requires **Python 3.10+**. The core is lightweight (`networkx` + `PyYAML`);
the MCP server and the JS plugin are opt-in extras.

```bash
pip install codetruth                 # CLI + Python API (dead-code gate, CI, scripts)
pip install "codetruth[mcp]"          # + the agent-facing MCP server
pip install "codetruth[javascript]"   # + the JS/TS plugin
pip install "codetruth[mcp,javascript]"   # everything (or: codetruth[all])
```

- **Not using an agent?** Plain `pip install codetruth` is all you need — the
  CLI (`codetruth scan`), Python API (`from codetruth import scan`), HTML
  report, and `--ci` gate work with no extra dependencies. The `mcp` extra
  pulls a web-server stack (pydantic/starlette/uvicorn) and is only for the
  MCP server, so the core deliberately doesn't require it.
- **Using it with Claude Code / an MCP agent?** Install `"codetruth[mcp]"`,
  then `claude mcp add codetruth -- codetruth mcp`.
- If the `codetruth` command isn't found, your Python scripts dir isn't on
  PATH — use `python -m codetruth.cli` (and `python -m codetruth.mcp_server`).

## MCP (the primary interface — for agents)

```bash
pip install "codetruth[mcp]"
claude mcp add codetruth -- codetruth mcp
```

Tools exposed: **`check_deletion_safety(repo, symbol)`** (the one to call
before deleting), **`scan(repo, ...)`** (the whole review queue),
**`plan_deletion(repo, symbol)`** (advisory removal plan), and
**`scan_workspace(repos, ...)`** (cross-service usage across repos). The agent
workflow: identify symbol → call `check_deletion_safety` → only delete on
`safe_to_delete`; everything else routes to human review.

## CLI

```bash
codetruth scan ./repo                     # review queue, strongest candidates first
codetruth scan ./repo -v --json out.json  # full evidence
codetruth scan ./repo --app-mode          # application (not library) repos:
                                          # public symbols may be safe_to_delete
codetruth scan ./repo --strict            # flag orphaned "useless clumps"
codetruth scan ./repo --min-rank 0.5 --group   # trim the tail, group by file
codetruth scan ./repo --html report.html  # self-contained HTML report
codetruth scan ./repo --ci                # exit 1 if dead code exists (report gate)
codetruth scan ./repo --progress          # live progress line (auto on a TTY)
codetruth baseline ./repo                 # accept current findings (see below)
codetruth check ./repo pkg.module:func    # one symbol's evidence record
codetruth plan  ./repo pkg.module:func    # advisory deletion plan (never applied)
```

Long scans show a live progress line (files scanned, then graph/rules/verify
phases) on a terminal; it's auto-silenced when output is piped (`--progress` /
`--no-progress` to override). Ctrl+C cancels cleanly.

The `--ci` gate is advisory like everything else: it *fails the build* so a
human looks at provably-dead code — it never deletes. Mark false alarms with
`# codetruth: keep` or a `.codetruth.toml` entrypoint.

## Adopting on an existing codebase (baseline)

A gate that fails on all *pre-existing* dead code never gets switched on.
Accept the current state once, commit the baseline, and the gate only fails
on **newly introduced** dead code:

```bash
codetruth baseline ./repo        # writes .codetruth.baseline.json — commit it
codetruth scan ./repo --ci       # now fails ONLY on new safe_to_delete code
```

The baseline keys on symbol ids, so line churn doesn't invalidate it. A
previously-hedged symbol whose deadness *becomes provable* (its last caller
was removed) counts as new. When accepted findings get cleaned up, the gate
tells you to refresh with `codetruth baseline`.

## What gets scanned (scope)

CodeTruth scans the directory you point it at. It **never descends into**
dependency, VCS, build, or environment folders — they're pruned from the walk
(so they don't slow it down or pollute results): `node_modules`, `.git`/`.hg`/
`.svn`, `.venv`/`venv`/`env`/`virtualenv`, `site-packages`, `__pycache__`,
`build`/`dist`/`.eggs`/`wheels`, the various caches, and vendored-code dirs
(`vendor`, `third_party`, `_vendor`, `vendored`). So a virtualenv or installed
package left inside your repo won't be treated as your code.

To exclude your own folders (generated code, migrations, fixtures), add a
`.codetruth.toml` at the repo root:

```toml
[codetruth]
ignore_paths = ["generated/", "migrations/", "**/fixtures/**"]
```

Ignored folders are pruned from the walk too, so excluding a large directory
also makes the scan faster. To scan just one package of a monorepo, point
`codetruth scan` at that package's directory.

## Python API

```python
from codetruth import scan, check_deletion_safety

result = scan("./repo")
for rec in result.candidates():
    print(rec.status.value, rec.symbol, rec.evidence_against_deletion)
```

## Cross-repo / cross-service (workspace scan)

Single-repo analysis can't see that an endpoint is called over the wire or a
shared package is imported by a sibling service — the exact usage that makes
distributed deletion dangerous. Scan several repos as one system:

```bash
codetruth workspace ./service-api ./service-worker ./shared-lib
```

```python
from codetruth import scan_repos
ws = scan_repos(["./service-api", "./service-worker"])
for xref in ws.crossrefs:
    print(xref.symbol, "<-", xref.reason)
```

It matches HTTP **routes to client calls** (a FastAPI/Flask route linked to a
`requests`/`httpx` call in another repo, path templates and params normalized)
and **shared imports** across repos. A symbol that looks dead in its own repo
but is reached cross-repo is raised from `likely_dead`/`safe_to_delete` to
`uncertain_dynamic_risk` with an explicit reason — the overlay only ever moves
a verdict toward *keep*. Also exposed as the `scan_workspace` MCP tool.

## Runtime evidence (v1.5)

Static analysis can't see cross-service usage (HTTP calls, queues, cron in
other repos). `@codetruth.track` logs real invocations in production:

```python
import codetruth

@codetruth.track
def maybe_dead(): ...
```

Or instrument a whole package with zero source edits:

```python
import codetruth.runtime
codetruth.runtime.instrument_package("myapp")   # or CODETRUTH_AUTOTRACK=myapp
```

Then feed the trace back: `codetruth scan ./repo --runtime-log runtime.jsonl`.
Observed calls promote a symbol to `definitely_used`; *"0 calls over N days"*
becomes the strongest evidence tier for deletion.

Tracing is production-safe: each process writes its own `runtime-<pid>.jsonl`
(merged at read — no lock contention between workers), and a daemon thread
flushes counts every `$CODETRUTH_FLUSH_INTERVAL` seconds (default 60), so
long-running servers land evidence without a clean exit.

## Finding useless clumps (strict reachability)

`codetruth scan ./repo --strict` asks a harder question: *is this code
reachable from any real entry point* (HTTP route, CLI command, `__main__`,
test, declared entrypoint)? Code that is internally well-connected — functions
calling each other — but never reached from an entry point surfaces as an
orphaned clump, with every member carrying a `cluster` field listing its
fellow members so the whole island can be reviewed (and deleted) as a group.
Dead-cluster grouping also applies in default mode whenever unreachable
symbols reference each other.

## Configuration (`.codetruth.toml`)

Teach the scanner about usage it can't see:

```toml
[codetruth]
app_mode = true                    # public symbols are internal (application)
entrypoints = [                    # externally-reached symbols (cron, RPC, ...)
    "jobs.nightly:run",
    "services.handlers.*",
]
ignore_paths = ["migrations/", "vendor/**"]
```

Inline: a `# codetruth: keep` comment on (or above) a definition marks it as
an entry point.

## Deletion plans (advisory)

`codetruth plan ./repo pkg.mod:symbol` (also the `plan_deletion` MCP tool, and
attached automatically to every `safe_to_delete` record) describes exactly what
a removal would involve: the decorator-to-end source span, imports that become
orphaned, and any `__all__` entry. CodeTruth **never applies** a plan — it is
information for whoever decides.

## Review-queue ranking

Every record carries a `rank_score` in `[0, 1]` — a deterministic ordering
heuristic (not a calibrated probability; see PLAN.md §4). Higher means weaker
evidence of use, so `scan()` and the CLI surface the strongest deletion
targets first. Within `uncertain_dynamic_risk` it separates a lone
string-literal reference from forty fuzzy attribute-name matches, so a big
review queue is triageable instead of flat.

## Performance

Scans are cached at `<repo>/.codetruth/index.json`, keyed by a fingerprint of
every source and config file's `(mtime, size)`. An unchanged repo returns the
cached result (≈15× faster on an 8k-symbol repo); any file change triggers a
full rescan. The cache never patches the graph incrementally — a stale
cross-file edge could mask a real usage path, so correctness always wins.
Bypass with `--no-cache` (CLI) or `force_rescan` (MCP). Add `.codetruth/` to
`.gitignore`.

## Architecture

```
Layer 1  Symbol Extraction    codetruth/languages/python/extractor.py
Layer 2  Relationship Graph   codetruth/languages/python/edges.py   (strong/weak edges)
Layer 3  Semantic Rules       codetruth/languages/python/rules.py + codetruth/rules/python/*.yaml
Layer 4  Evidence + Decision  codetruth/core/evidence.py            (4-way status)
```

The core engine is language-agnostic (`codetruth/core/`, `LanguagePlugin`
interface).

**Python** is the full plugin. Framework awareness covers FastAPI/Flask/
Starlette routes, Django (signals, URLs, admin, management commands,
migrations), Celery, click/Typer, pytest, SQLAlchemy events, and — as of
0.5.0 — **declarative schema models**: fields of pydantic `BaseModel`/
`BaseSettings`/`SQLModel`, Django models/forms, DRF serializers,
`TypedDict`/`NamedTuple`, and marshmallow/msgspec are treated as framework-used
(populated, validated and serialized, not referenced like ordinary attributes),
transitively through subclasses, with the `Config`/`Meta` convention honoured.
Function-signature annotations (`def f(u: User) -> Order`) create usage edges,
so a model referenced only in type hints stays alive. New framework rules go
in `codetruth/rules/python/*.yaml` — no code changes.

**JavaScript/TypeScript** (`pip install "codetruth[javascript]"`, then
`scan --language javascript`): tree-sitter extraction, ESM/CommonJS import
resolution, **tsconfig/jsconfig `paths` + `baseUrl` aliases and monorepo
workspace packages**, **barrel re-export chains**, **Vue SFC (`.vue`)
scripts**, package.json entry points (incl. `scripts`), Express/Fastify/emitter
callback handlers, React/JSX component and event-handler usage, string/config
wiring, eval poisoning, and external-base cautions — the shared evidence,
ranking, cluster, backstop, and cache layers work unchanged. Validated on real
apps (RealWorld React, preact, jupyterlab) with zero false positives; a
hand-labelled JS *recall* study is the remaining polish. **Go** is a stub.

## Validation

The metric that matters is **false positives** — a symbol called
`safe_to_delete` that's actually used. Across 10 real Python packages
(requests, flask, click, jinja2, werkzeug, rich, pydantic, urllib3,
sqlalchemy, networkx — **36,457 symbols**), the false-positive audit is
**0** — and it still finds genuine dead code (e.g. `urllib3._url_from_pool`,
`rich._svg_hash`, `requests.dict_to_sequence`). Empirical calibration on
labelled data: `safe_to_delete` is 100% dead, monotone across tiers. JS is
validated on the RealWorld React app and preact (0 unsafe verdicts).
Reproduce with `scripts/validation_report.py`; details in
[`validation/VALIDATION.md`](validation/VALIDATION.md).

## Known limitations

- Cross-service usage is invisible to static analysis alone — runtime tracing
  is the partial fix.
- 100% certainty is impossible; `safe_to_delete` means "no usage path found
  under the defined rules," not a mathematical proof.
- Framework rule coverage (Layer 3) is a maintained knowledge base, never
  finished. New rules go in `codetruth/rules/python/*.yaml` — no code changes.
