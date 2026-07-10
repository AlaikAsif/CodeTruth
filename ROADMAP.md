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

## Milestone v1.1 — "Sharper, more actionable advice"  ✅ SHIPPED 2026-07-09

Still purely advisory — this milestone makes the *advice* better: it tells the
reader exactly what a deletion would involve, catches more genuinely-dead code,
and lets users teach it about their entrypoints. Nothing here modifies code.

Shipped in full, plus one item added mid-flight by request: **strict
reachability mode** (`scan --strict`) — roots become only real entry points so
internally-connected but orphaned clumps ("useless clumps") surface, grouped
via each record's `cluster` field.

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

### 1.2.1 — PyPI publish  · S · _no deps_  ✅ SHIPPED 2026-07-10
Live: https://pypi.org/project/codetruth/ (0.2.0, wheel + sdist, published
via Trusted Publishing on the v0.2.0 tag). Verified with a clean-venv
`pip install codetruth[mcp]` → CLI scan produces correct verdicts.

### 1.2.2 — Recall validation at real scale (PLAN §9)  · M · _no deps_  · [~] started
The constructed repo proves the patterns, not scale. Hand-verify ~50 symbols
each across 5 real repos; store label files; run `scripts/measure_recall.py`.
**Done so far:** `tests/validation/real/` holds grep-verified starter sets for
jinja2 (15 symbols, incl. the template-only `Cycler.reset` and Babel
entry-point `babel_extract` traps) and click (4 symbols, incl. the stdlib
override trap). **Remaining:** grow to ~50/repo across 5 repos + written
error analysis.
- **Done when:** documented aggregate: 0 FP, recall per repo, error analysis.

### 1.2.3 — Runtime-tracing hardening (v1.5)  · M · _no deps_  ✅ SHIPPED 2026-07-09
Per-pid log files merged at read (multiprocess-safe, verified with two
concurrent subprocesses), daemon interval flushing (survives hard exits),
and `instrument_package()`/`autotrack()` auto-instrumentation via import
hook — a package can be traced with zero source edits.
**Remaining (folded into 2.3):** multi-service fleet log merge.

---

## Milestone v2 — "Beyond one language, one repo"

### 2.1 — Numeric confidence calibration  · M · _needs 1.2.2_
Replace the heuristic `rank_score` with a probability calibrated from the
labeled corpus (e.g. isotonic/Platt over the current signals). Keep the
heuristic as the cold-start default; only ship a number once it's backed by
data, per PLAN §4.

### 2.2 — JavaScript/TypeScript plugin  · XL · _no deps_  · [~] beta shipped 2026-07-09
Beta shipped: tree-sitter extraction (JS/TS/TSX), ESM + CommonJS import
resolution, namespace/member/`this.` strong edges, property name-match weak
edges, package.json main/bin/exports entry points, string/config wiring,
eval poisoning, external-base cautions. **Framework coverage added:**
Express/Fastify/emitter callback entry points, package.json `scripts`
targets, and React/JSX component + event-handler resolution. 22 ground-truth
tests; smoke-tested on jupyterlab's JS (387 symbols, 0 parse errors, 0 unsafe
verdicts).
**Beta-exit progress (2026-07-10):** tsconfig/jsconfig `paths`+`baseUrl` alias
resolution, monorepo workspace-package resolution, and Vue SFC (`.vue`) script
extraction all shipped (10 tests; jupyterlab still 0 unsafe/0 warnings).
**Remaining to fully leave beta:** re-export chain fidelity at scale and a
JSX-heavy real-repo validation with hand labels.

### 2.3 — Cross-repo scanning  · L · _no deps_  · [~] shipped 2026-07-10
The real static answer to cross-service usage. `codetruth workspace repoA
repoB …` / `scan_repos()` / `scan_workspace` MCP tool scan multiple repos as
one system and overlay cross-service usage: HTTP route↔client matching
(FastAPI/Flask routes linked to requests/httpx calls, path params normalized)
and shared imports across repos. A symbol dead in its own repo but reached
cross-repo is raised to `uncertain_dynamic_risk`; the overlay only moves
verdicts toward keep. 8 tests + two-service fixtures; live-demoed. Turns
"invisible to static analysis" (PLAN §10) into "visible across the system."
**Remaining:** true unified graph (vs the current evidence overlay), queue/
RPC/gRPC method-name linking, JS cross-repo surface (currently Python-only),
and OpenAPI/spec-driven route discovery.

### 2.4 — Go plugin  · XL · _no deps_
Second compiled-language plugin once the JS plugin has validated the extension
model on a non-Python ecosystem.

---

## Continuous tracks (no single "done")

### Python analysis robustness  · ongoing
Covered: star re-export chains, `TYPE_CHECKING` blocks, `__main__` packages,
`singledispatch`. Still open, each a fixture + targeted fix as real repos
surface them: descriptors/`__set_name__`, monkeypatching, some relative-import
edge cases at package boundaries, implicit-namespace-package corner cases.

### Layer 3 rule coverage  · ongoing  · [~] growing
The framework knowledge base is never finished (PLAN §10). Shipped: FastAPI/
Flask, Django, Celery, click, pytest, **SQLAlchemy events/validators/hybrids,
Typer, Starlette, `__main__` entry points**; `singledispatch` recognized.
Still to add as they appear: Pydantic v2 validators, Airflow, pytest plugin
hooks, Sphinx directives.

### Signal quality & reporting  ✅ SHIPPED 2026-07-09
`scan --group` (by file), `--min-rank` (trim the low-signal tail), `--html`
(self-contained offline report), and `--ci` (advisory report gate: exit 1 on
provably-dead code; never deletes).

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
