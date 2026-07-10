# CodeTruth — Project Plan
### A verification layer that lets AI agents safely delete code in large codebases

> **Status note (2026-07-11):** this is the founding vision doc, kept as-is for
> the rationale it records. Much of the "future" here has shipped — Python +
> JavaScript/TypeScript plugins, MCP server, cross-repo scanning, runtime
> tracing, baseline/CI mode, schema-model awareness. For current state see
> [CHANGELOG.md](CHANGELOG.md) and [ROADMAP.md](ROADMAP.md); references like
> "JS/Go stubs" below describe the original v1 scope, not today's.

---

## 1. The Core Problem

**AI agents can't safely delete code in large codebases.**

Agents are increasingly trusted to refactor, clean up, and modernize code. But deletion is the one operation they can't do safely at scale, because:

- **They lack full execution knowledge of the repo** — and no knowledge at all of cross-service usage (a symbol may only be reached via an HTTP call, queue message, or cron job in another repo entirely).
- **They hallucinate absence of usage** — an agent will confidently declare code "unused" based on a partial view.
- **There's no deterministic verification step** before the deletion happens — the agent's judgment *is* the safety check, and it's unreliable.
- **The problem compounds with size.** The larger the codebase, the more indirect usage patterns (decorators, DI, registries, reflection, config-driven wiring), the more dynamic edges an agent can't see, and the bigger the blast radius of a single wrong deletion.

On a small script, an agent can eyeball usage. On a real production codebase, it's guessing — and guessing about deletion is how you break production.

**CodeTruth is the verification layer that fixes this.** The agent doesn't perform detection. It consults CodeTruth's evidence graph first, and only acts on symbols the graph cannot find any usage path for. The agent becomes a *reviewer*, not an *oracle*.

---

## 2. The Core Insight (the logic inversion)

Instead of asking *"is this code used?"*, the system asks:

> **"Can we prove this code is used?"**

A symbol is only ever surfaced for deletion when the system **fails to find any possible path of usage under its defined analysis rules.** This shifts the burden of proof onto *keeping* code rather than *deleting* it, and it's what makes the output trustworthy enough for an agent to act on. Detection is deterministic; the agent only interprets and decides.

---

## 3. Why Existing Tools Don't Solve This

Linters and dead-code detectors (`vulture`, `ts-prune`, `knip`, CodeQL, etc.) exist, but they fail as an agent-safety layer because:

- **No proof.** They say "probably unused" without evidence an agent can reason over.
- **No dynamic reasoning.** They ignore framework and runtime behavior — exactly the usage patterns that dominate large codebases.
- **No structured decision system.** They emit a flag, not a confidence-graded, evidence-backed record that separates detection from decision from execution.

CodeTruth introduces that separation explicitly:

```
1. Detection        (deterministic)
2. Uncertainty      (heuristics — framework/dynamic patterns)
3. Decision         (AI or human, reading evidence)
4. Execution safety (git + tests)
```

That separation — not any single layer — is the actual innovation.

---

## 4. Mental Model: Code as a Graph

Everything reduces to a directed dependency graph:

- **Nodes:** functions, classes, methods, variables, modules
- **Edges:** function calls, imports, inheritance, attribute access, registry bindings, string-based references

**A symbol is "dead"** = a node with no valid inbound edge, *after accounting for dynamic/indirect usage.*

Edges are classified by confidence:

| Type | Examples | Confidence |
|---|---|---|
| **Strong** | direct calls, explicit imports, inheritance | High |
| **Weak** | string references, `getattr`/reflection, framework hooks, config-driven usage | Uncertain |

This strong/weak split is the primitive that lets the tool distinguish "definitely safe" from "looks dead but there's a dynamic risk" — exactly the distinction an agent needs before deleting.

---

## 5. System Architecture — 4 Layers

```
Layer 1: Symbol Extraction     → "what exists"
Layer 2: Relationship Graph    → "what connects to what"
Layer 3: Semantic Safety Rules → "could this be used indirectly?" (the differentiator)
Layer 4: Evidence + Decision   → structured output an agent or human acts on
```

### Layer 1 — Symbol Extraction
Parse the codebase; extract every function, class, method, module-level variable, import, export into a flat symbol table: `{id, name, type, file, line, exported}`.

### Layer 2 — Relationship Graph
Build a directed graph (call / import / reference) connecting symbols, classifying each edge strong/weak at creation time. This is where most lint tools stop — CodeTruth doesn't.

### Layer 3 — Semantic Safety Analysis (the differentiator)
Detect framework/dynamic-usage patterns that create the indirect edges agents can't see:
- Route decorators (FastAPI endpoints, `@app.route`)
- Django signals, URL patterns
- Plugin/registry systems, DI containers
- Reflection (`getattr`/`setattr`)
- String-literal references (config, dict-based dispatch)

Produces a 4-way classification, not a binary: `safe_to_delete`, `likely_dead`, `uncertain_dynamic_risk`, `definitely_used`.

### Layer 4 — Evidence + Decision Layer
For every candidate symbol, assemble a record an agent can reason over (evidence for/against deletion, risk level, recommended action). The agent (or human) reads this and decides. Detection is never left to the LLM — that's the entire point.

---

## 6. Primary Interface: The Agent Path (MCP)

- **MCP server:** exposes CodeTruth as a tool so Claude Code or any MCP-capable agent can call `check_deletion_safety(symbol)` or `scan(repo)` before acting. Primary distribution channel.
- **Agent workflow:** agent identifies symbol → calls CodeTruth via MCP → reads evidence record → only deletes on `safe_to_delete`; anything else routes to human review.
- **Python API** (`from codetruth import scan`) and **CLI** (`codetruth scan ./repo`) exist for humans and scripts.

---

## 7. Language Scope: Design Multi-Language, Ship Python First

Layers 1, 2, 4 generalize; Layer 3 does not — each ecosystem needs its own rule library. Core engine is language-agnostic via a `LanguagePlugin` interface; **only Python fully implemented in v1** (FastAPI + Django rule coverage). Adding a language later = "write a plugin."

---

## 8. Build Plan (Phased)

- **Phase 1 — Symbol Extraction:** AST walk for all definitions, imports, exports. Decorators, nested functions, `__init__.py` re-exports, `__all__`.
- **Phase 2 — Relationship Graph:** directed graph (networkx); call/import/inheritance/attribute edges classified strong/weak at creation.
- **Phase 3 — Semantic/Dynamic Rules (the hard part):** plugin rule system; FastAPI routes, Django signals/URLs, `getattr` reflection, string-literal refs. YAML-defined where possible.
- **Phase 4 — Evidence + Decision Layer:** JSON evidence schema. 4-value enum first, no fabricated confidence float.
- **Phase 5 — Interfaces:** CLI, Python API, MCP server (priority).
- **Phase 6 — Runtime Evidence (v1.5):** `@codetruth.track` logging real invocations; "zero calls over N days in production" = strongest evidence tier; the only real answer to cross-service usage.

---

## 9. Validation Strategy (do not skip)

- 3–5 real Python repos (plain, FastAPI, Django); manual ground truth ~50 symbols each.
- Measure **false positives** separately from false negatives.
- **False positives are the metric that matters.** Default conservative under uncertainty: `uncertain_dynamic_risk`, never `safe_to_delete`.

---

## 10. Known Limitations

- Cross-service usage is invisible to static analysis alone — Phase 6 runtime tracing is the partial fix.
- 100% certainty is impossible. This is a *risk assessor for code deletion*, not a "dead code detector."
- Layer 3 coverage is never finished — a maintained knowledge base, not a solved algorithm.

---

## 11. Why This Matters

Most tools optimize for *adding* code safely. CodeTruth optimizes for *removing* it safely — rarer, harder, higher-impact. It gives AI agents a deterministic verification layer so they can finally delete code in large codebases without guessing.
