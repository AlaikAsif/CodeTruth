# Changelog

## 0.3.0 — Unreleased

### Added
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
  only via JSX is correctly seen as used. Validated on jupyterlab's JS
  (387 symbols, 0 false positives).

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
