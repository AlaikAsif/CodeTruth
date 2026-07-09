# Changelog

## 0.2.0 — 2026-07-09

Advisory-only is now an explicit, documented guarantee: CodeTruth never
modifies code; it emits evidence and plans for a human or agent to act on.

### Added
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
