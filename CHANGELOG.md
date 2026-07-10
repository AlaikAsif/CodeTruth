# Changelog

## 0.5.0 — 2026-07-11

### Added
- **Schema-model awareness** — fields of declarative models (pydantic
  `BaseModel`/`BaseSettings`/`SQLModel`, Django `Model`/forms, DRF
  serializers, `TypedDict`/`NamedTuple`, marshmallow/msgspec, transitively
  through subclasses) are treated as framework-used: they're populated,
  validated and serialized by the framework, not referenced like ordinary
  attributes. The `Config`/`Meta` nested-class convention is recognised too.
  A dead *model* is still flagged at the class level.
- **Signature annotation edges** — `def f(u: User) -> Order` now creates
  usage edges to `User` and `Order` (FastAPI-style code often references a
  model only in annotations); field annotations keep nested models alive.

On the pydantic codebase this moves ~250 symbols out of the review queue into
`definitely_used`; the false-positive audit stays at 0.

## 0.4.0 — 2026-07-11

### Added
- **Baseline / CI adoption mode** — `codetruth baseline ./repo` accepts every
  current finding into `.codetruth.baseline.json` (commit it); from then on
  `codetruth scan --ci` fails **only on newly introduced** provably-dead
  code, listing exactly the new symbols. Keys on symbol ids so line churn
  doesn't invalidate it; a hedged symbol whose deadness becomes provable
  counts as new; resolved findings are reported so the baseline can be
  refreshed. `--baseline PATH` overrides the default location. The baseline
  file is excluded from string-reference scanning (it's a list of symbol ids
  — scanning it would make every accepted finding look referenced).

## 0.3.2 — 2026-07-10

### Fixed
- **Scan timeout on repos with `node_modules`/`.git`/venvs.** File discovery
  used `rglob`, which walked the *entire* tree and only filtered skipped
  directories from the results — so a large `node_modules` or `.git` was fully
  traversed (3× per scan) even though nothing in it was scanned. Now the walk
  is **pruned** (`os.walk` with in-place dir filtering): 16k skipped files went
  from ~5s to ~0s. Same fix for the JS extractor and the monorepo resolver.

### Changed
- More directories skipped by default: `virtualenv`, `.hypothesis`, `htmlcov`,
  `wheels`, and vendored-code dirs (`vendor`, `vendored`, `third_party`,
  `_vendor`). Configured `ignore_paths` now prune the traversal too, so
  excluding a big folder also speeds up the scan.

## 0.3.1 — 2026-07-10

### Changed
- **Core stays lightweight; `mcp` is an optional extra again.** 0.3.0 briefly
  made `mcp` a core dependency, which forced a whole web-server stack
  (pydantic/starlette/uvicorn, ~14 packages) onto CLI / CI / library users who
  never run the MCP server. Reverted: `pip install codetruth` is lean (just
  `networkx` + `PyYAML`); the agent-facing server is `pip install
  "codetruth[mcp]"` (or `codetruth[all]`). `codetruth mcp` now prints an
  actionable install hint when the extra is missing. Requires Python 3.10+.

## 0.3.0 — 2026-07-10

Published to https://pypi.org/project/codetruth/ via Trusted Publishing.

### Added
- **JS module resolution for real projects** — `tsconfig.json`/`jsconfig.json`
  `paths` + `baseUrl` aliases (`@/utils`, `~lib/*`), parsed leniently (JSONC
  comments and trailing commas), and monorepo workspace packages (imports of a
  sibling package by its `package.json` name). Aliased imports now link, so
  their targets are no longer mistaken for unused.
- **Barrel re-export chains** — imports through an `index.ts` barrel
  (`export { X } from './x'`, `export * from './x'`) resolve to the real
  defining symbol. A re-export is treated as a pass-through, not a use, so a
  re-exported-but-unused symbol is honestly `likely_dead`, not spuriously kept.
- **Vue SFC support** — `.vue` single-file components: the `<script>` block is
  extracted and analyzed (imports, functions, usage) with original line
  numbers preserved; `<template>`/`<style>` are ignored.
- **Cross-repo / workspace scanning** (`codetruth workspace repoA repoB ...`,
  `scan_repos()`, `scan_workspace` MCP tool) — scans multiple repos as one
  system and overlays cross-service usage single-repo analysis can't see:
  HTTP **route↔client matching** (a FastAPI/Flask route linked to a
  `requests`/`httpx` call in another repo, path params normalized) and
  **shared imports** across repos. A symbol dead in its own repo but reached
  cross-repo is raised to `uncertain_dynamic_risk` with an explicit reason;
  the overlay only ever moves a verdict toward keep, never toward delete.
- **JS framework coverage** — callback/route entry points (Express/Fastify
  `app.get/post/use/...`, event emitters `.on/.once/.addEventListener`,
  `server.listen`): the handler passed to a registration call is marked used
  and survives strict mode. `package.json` `scripts` targets (`node
  src/server.js`) join main/bin/exports as module entry points. React/JSX
  component usage (`<UserCard/>`, `<LocalBadge/>`) and event-handler
  references (`onClick={fn}`) resolve to their symbols — a component rendered
  only via JSX is correctly seen as used.

### Validation
- **0 false positives across 36,457 symbols** in 10 real Python packages
  (requests, flask, click, jinja2, werkzeug, rich, pydantic, urllib3,
  sqlalchemy, networkx); found genuine dead code (e.g. `urllib3._url_from_pool`,
  `rich._svg_hash`, `requests.dict_to_sequence`). Empirical calibration:
  `safe_to_delete` P(dead)=1.00, monotone across tiers. JS validated on the
  RealWorld React app (209 symbols, 0 unsafe) and preact (1349 symbols, 0
  parse errors). See `validation/`.

## 0.2.0 — 2026-07-10 (first PyPI release)

Published to https://pypi.org/project/codetruth/ via Trusted Publishing.

### Added
- **JavaScript/TypeScript plugin (beta)** — `pip install codetruth[javascript]`
  then `codetruth scan ./repo --language javascript`. tree-sitter extraction
  (functions, classes, methods, vars, interfaces/enums), ESM + CommonJS import
  resolution with relative-path module linking, namespace/member/`this.`
  strong edges, property name-match weak edges, package.json
  main/bin/exports entry points, string/config wiring, `eval`/`new Function`
  poisoning, and external-base cautions. The shared core (graph, evidence,
  rank_score, clusters, textual backstop, cache, .codetruth.toml) applies
  unchanged — proving the LanguagePlugin extension path end-to-end.
- Reporting/DX for `codetruth scan`: `--min-rank` (trim the low-signal tail),
  `--group` (group output by file), `--html` (self-contained offline report),
  and `--ci` (exit non-zero when provably-dead code exists — an advisory
  report gate that never deletes).
- Layer-3 rule packs: SQLAlchemy (`listens_for`, `validates`, hybrid/declared
  attributes), Typer commands, Starlette routes, and `__main__.py` entry
  points. `functools.singledispatch` registrations are recognized as used.

Advisory-only is an explicit, documented guarantee: CodeTruth never
modifies code; it emits evidence and plans for a human or agent to act on.

- **Dead-cluster elimination** — liveness reachability over strong edges; a
  strong reference only proves use when its source is itself reachable. Dead
  code referenced only by other dead code is surfaced (`likely_dead` with
  cluster evidence) instead of hiding as used.
- **Strict reachability mode** (`scan --strict`, `reachability="strict"`,
  MCP `strict=true`) — liveness roots become only real entry points, so
  internally-connected but orphaned clumps surface for review.
- **Cluster grouping** — unreachable symbols carry a `cluster` field listing
  their fellow clump members; islands are reviewed as a group.
- **Advisory `deletion_plan`** — exact decorator-to-end span, imports that
  become orphaned, `__all__` entries. On every `safe_to_delete` record, via
  `codetruth plan`, `plan_deletion()`, and the `plan_deletion` MCP tool.
- **`.codetruth.toml`** — `app_mode`, declared `entrypoints` (cron/RPC
  handlers the graph can't see), `ignore_paths`; plus inline
  `# codetruth: keep` suppression.
- **`rank_score`** — deterministic review-queue ordering (not a calibrated
  probability) that spreads the uncertain tier by weak-edge kind.
- **Persistent scan cache** — `<repo>/.codetruth/index.json` keyed by a
  (mtime, size) fingerprint of all source/config files (~15× faster on
  unchanged repos), with graceful degradation on read-only trees.
- **Validation harnesses** — real-repo false-positive audit
  (`scripts/validate_real_repos.py`) and labeled-recall measurement
  (`scripts/measure_recall.py` + `tests/validation/`): 0 false positives,
  recall@queue 6/6 on the constructed ground-truth repo.
- LICENSE (MIT), GitHub Actions CI (3.10–3.12 + self-scan).

## 0.1.0 — 2026-07-08

Initial release: 4-layer engine (AST symbol extraction, strong/weak
relationship graph, semantic safety rules with YAML packs for
FastAPI/Flask/Django/Celery/click/pytest, evidence + 4-way decision layer),
textual verification backstop, CLI, Python API, MCP server, and
`@codetruth.track` runtime tracing.
