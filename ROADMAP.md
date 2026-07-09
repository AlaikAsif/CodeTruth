# CodeTruth — Roadmap

Everything remaining after v1 + the first four post-v1 items (LICENSE/CI,
evidence ranking, persistent cache, recall validation) shipped. See PLAN.md
for the original design and rationale; this file is the forward plan.

Status legend: **[ ]** not started · **[~]** partial · effort **S** (≤1 day)
· **M** (2–4 days) · **L** (1–2 weeks) · **XL** (3+ weeks).

---

## Guiding constraint (unchanged)

False positives are the only metric that can kill the tool. Every item below
must preserve the invariant: **`safe_to_delete` is never emitted when any
usage path exists.** New analysis may only *narrow* what qualifies as safe —
never loosen the guarantee to gain recall.

## Scope decision: advisory only

**CodeTruth never modifies code.** It does not delete symbols, edit files,
stage git commits, or run an "apply" step — not via CLI, not via MCP. It
produces evidence and recommendations; a human or agent reads them and decides
what to do. This is deliberate: an automated deleter that is ever wrong is
exactly the production incident the tool exists to prevent, so we keep the
blast radius at zero by never acting. Every item below is analysis, evidence,
or reporting — nothing performs the deletion. (The shipped v1 already works
this way: no code path writes to the repo.)

---

## Milestone v1.1 — "Sharper, more actionable advice"

Still purely advisory — this milestone makes the *advice* better: it tells the
reader exactly what a deletion would involve, catches more genuinely-dead code,
and lets users teach it about their entrypoints. Nothing here modifies code.

### 1.1.1 — `deletion_plan` (advice: what a deletion would involve)  · M · _no deps_
The record says "delete" but not *what to remove*. Add an advisory
`deletion_plan` to every `safe_to_delete` (and, on request, any) record so the
human/agent reading it knows the full scope before they act:
- exact source span: decorators → end of block, plus trailing blank lines;
- **newly-orphaned imports**: imports whose only remaining user is the symbol
  being described (compute from the graph — an import edge with no other live
  consumer);
- `__all__` entry and `__init__.py` re-export that would also need removing;
- note when every symbol in a module is safe (whole-module candidate).
- This is *description*, not execution: CodeTruth emits the plan; it never
  applies it.
- Files: new `codetruth/core/deletion.py`; expose on `EvidenceRecord`; surface
  via CLI (`codetruth plan <symbol>`) and a new MCP field.
- **Done when:** for a fixture with an import used only by a dead function, the
  plan lists both the def span and that import line.

### 1.1.2 — Dead-cluster elimination (iterative reachability)  · M · _no deps_
Today a strong edge from *dead* code keeps its target alive (the
`_legacy_transform` miss in validation). Replace "has any inbound edge" with
"reachable from a live root":
- roots = entrypoint/runtime markers + non-test strong references from live
  code + public API (in library mode) + explicitly declared entrypoints (1.1.4);
- compute reachability over strong edges; symbols not reached are dead even if
  referenced only by other unreached symbols;
- weak edges and the dynamic-module poison rule still apply on top
  (conservative: reachability only *demotes*, never overrides a weak signal).
- Changes: a reachability pass feeding `evidence.py`.
- Note: the cluster *interior* is surfaced as `likely_dead` with cluster
  evidence, never `safe_to_delete` standalone — deleting only the interior
  would break its (dead) caller, so the sound advice is "delete as a group."
- **Done when:** validation recall@queue goes 5/6 → 6/6 with zero dead symbols
  hiding as definitely_used, and the FP audit on yaml/jinja2/click/networkx
  stays at **0**. ✅ shipped

### 1.1.3 — Config + declared entrypoints + ignores  · S · _no deps_
Let users teach the tool about usage it can't see. Add `.codetruth.toml`:
- `entrypoints = [...]` — symbols/globs the user declares externally reached
  (cron jobs, service runners, cross-repo HTTP handlers) → treated as live
  roots, killing false `likely_dead`;
- `ignore_paths = [...]`; `app_mode = true|false`;
- inline `# codetruth: keep` suppression comment.
- **Done when:** a declared entrypoint flips from `likely_dead` to
  `definitely_used`, and an ignored path is absent from results.

---

## Milestone v1.2 — "Installable and proven at scale"

### 1.2.1 — PyPI publish  · S · _no deps_
Nobody can `pip install codetruth` yet. Add build metadata polish, a
`CHANGELOG.md`, and a GitHub Actions release job using PyPI Trusted Publishing
(OIDC, no stored token) triggered on a version tag.
- **Done when:** `pipx install codetruth` works from a clean machine and
  `codetruth mcp` runs.

### 1.2.2 — Recall validation at real scale (PLAN §9)  · M · _no deps_
The constructed repo proves the patterns, not scale. Hand-verify ~50 symbols
each across 5 real repos (plain, FastAPI, Django, a CLI app, a library); store
label files; run `scripts/measure_recall.py`. Separate FP (must stay 0) from
recall; tune weak-edge weights and cluster reachability against the results.
- **Done when:** a `validation/real/*.json` label set exists with a documented
  aggregate: 0 FP, recall numbers per repo, and a written error analysis.

### 1.2.3 — Runtime-tracing hardening (v1.5)  · M · _no deps_
`@codetruth.track` is single-process toy-grade. Make it production-real:
- multiprocess-safe log (per-pid files merged at read, or file locking);
- import-hook / `sys.setprofile` mode to auto-instrument a whole package
  instead of decorating functions one by one;
- time-window flushing (not only per-1000-calls / atexit);
- multi-service log merge so "0 calls over N days" aggregates across a fleet.
- **Done when:** two concurrent processes tracking the same package produce a
  correctly merged call count, and a package can be traced with zero source
  edits.

---

## Milestone v2 — "Beyond one language, one repo"

### 2.1 — Numeric confidence calibration  · M · _needs 1.2.2_
Replace the heuristic `rank_score` with a probability calibrated from the
labeled corpus (e.g. isotonic/Platt over the current signals). Keep the
heuristic as the cold-start default; only ship a number once it's backed by
data, per PLAN §4.

### 2.2 — JavaScript/TypeScript plugin  · XL · _no deps_
The largest single effort and the most-requested second language. A full
`LanguagePlugin`: extractor (tree-sitter or the TS compiler API), edge builder,
and a JS-ecosystem rule pack (Express/Fastify routes, React/Vue components,
`package.json` bin/exports entrypoints, DI, knip-style config wiring). Layers
1/2/4 already generalize; this is Layer 3 rebuilt for the ecosystem.

### 2.3 — Cross-repo scanning  · L · _no deps_
The real static answer to cross-service usage. Scan multiple repos into one
graph and link across them: an HTTP route in repo A ↔ a client call in repo B,
queue names, RPC method names. Turns "invisible to static analysis" (PLAN §10)
into "visible when you point it at the whole system."

### 2.4 — Go plugin  · XL · _no deps_
Second compiled-language plugin once the JS plugin has validated the extension
model on a non-Python ecosystem.

---

## Continuous tracks (no single "done")

### Python analysis robustness  · ongoing
Namespace/implicit-namespace packages; star re-export chains; `TYPE_CHECKING`
type-only imports; descriptors/`__set_name__`; monkeypatching; relative-import
edge cases at package boundaries; `functools.singledispatch` registration.
Each is a fixture + a targeted fix; grow coverage as real repos surface gaps.

### Layer 3 rule coverage  · ongoing
The framework knowledge base is never finished (PLAN §10). Add YAML rule packs
as ecosystems appear: SQLAlchemy events, Pydantic v2, Typer, Starlette,
Airflow, pytest plugins, setuptools entry-points, Sphinx directives.

### Signal quality & reporting  · S each
Group the review queue by module/file; `--min-rank` threshold; an HTML report;
a `--ci` mode that exits non-zero on new `safe_to_delete` (a dead-code *report*
gate — it fails the build for a human to look, it does not delete anything).

---

## Dependency order (critical path)

```
1.1.1 deletion_plan (advisory)      (independent)
1.1.2 dead-cluster ──────────────────► 1.2.2 real-scale recall ─► 2.1 calibration
1.1.3 config/entrypoints             (independent)
1.2.1 PyPI                           (independent, ship anytime)
2.2 JS · 2.3 cross-repo · 2.4 Go     (independent, large)
```

## Recommended sequence

1. **1.1.2 dead-cluster elimination** — the biggest accuracy win, takes
   validation recall to 6/6 while holding FP at 0.
2. **1.1.1 deletion_plan** and **1.1.3 config/entrypoints** — make the advice
   more complete and let users suppress known entrypoints. Small, high-signal.
3. **1.2.1 PyPI** — small, unblocks external adoption.
4. **1.2.2 recall at scale**, then **2.1 calibration**.
5. **1.2.3 runtime hardening** when a distributed use case is real.
6. **2.2 JS plugin** as the big v2 bet; **2.3 cross-repo** as the moat.
