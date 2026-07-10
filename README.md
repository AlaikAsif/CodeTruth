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

```bash
pip install codetruth              # from PyPI
pip install codetruth[mcp]         # with the MCP server
pip install codetruth[javascript]  # with the JS/TS plugin (beta)
```

## MCP (the primary interface — for agents)

```bash
claude mcp add codetruth -- codetruth mcp
```

Tools exposed: `scan(repo_path, ...)` and `check_deletion_safety(repo_path, symbol)`.
The agent workflow: identify symbol → call `check_deletion_safety` → only delete
on `safe_to_delete`; everything else routes to human review.

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
codetruth check ./repo pkg.module:func    # one symbol's evidence record
codetruth plan  ./repo pkg.module:func    # advisory deletion plan (never applied)
```

The `--ci` gate is advisory like everything else: it *fails the build* so a
human looks at provably-dead code — it never deletes. Mark false alarms with
`# codetruth: keep` or a `.codetruth.toml` entrypoint.

## Python API

```python
from codetruth import scan, check_deletion_safety

result = scan("./repo")
for rec in result.candidates():
    print(rec.status.value, rec.symbol, rec.evidence_against_deletion)
```

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
interface). **Python** is the full v1 plugin (FastAPI, Django, Celery, click,
pytest, SQLAlchemy, Typer rule coverage). **JavaScript/TypeScript** is a beta
plugin (`pip install codetruth[javascript]`, then `scan --language javascript`):
tree-sitter extraction, ESM/CommonJS import resolution, package.json
entry points, string/config wiring, eval poisoning, and external-base cautions
— with the shared evidence, ranking, cluster, backstop, and cache layers
working unchanged. Go remains a stub.

## Known limitations

- Cross-service usage is invisible to static analysis alone — runtime tracing
  is the partial fix.
- 100% certainty is impossible; `safe_to_delete` means "no usage path found
  under the defined rules," not a mathematical proof.
- Framework rule coverage (Layer 3) is a maintained knowledge base, never
  finished. New rules go in `codetruth/rules/python/*.yaml` — no code changes.
