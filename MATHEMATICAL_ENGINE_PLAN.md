# CodeTruth Mathematical Analysis Engine — Implementation Plan

## 1. Purpose

CodeTruth currently models deletion safety with extracted symbols, strong and
weak graph edges, framework rules, reachability, runtime evidence, and an
independent textual backstop. This plan evolves that design into a conservative
program-analysis engine with an explicit mathematical foundation.

The target is not an impossible proof that a symbol can never be called in any
real-world environment. The target is a precise, auditable guarantee:

> Under the declared repository boundary and supported semantic model, a symbol
> is `safe_to_delete` only when the analysis has computed that no modeled
> execution can reach it.

The new engine should improve precision around dynamic calls, configuration,
framework wiring, aliases, and multi-step resolution without weakening the
existing safety invariant.

## 2. Product invariant

False-safe verdicts are the critical failure mode. The governing invariant is:

\[
\text{ConcreteReachable}(s) \Rightarrow \text{AbstractMayReachable}(s)
\]

Therefore:

\[
s \notin \text{AbstractMayReachable} \Rightarrow
s \notin \text{ConcreteReachable}
\]

The second implication is valid only when the first soundness condition holds
for the language features and repository boundary in scope. Unsupported or
open-world behavior must produce uncertainty, not safety.

CodeTruth remains advisory. This engine produces verdicts, evidence, and proof
certificates; it never modifies source code.

## 3. Why the current graph is not enough

A plain edge `f -> g` cannot naturally represent conditional or value-dependent
usage such as:

- `getattr(service, method_name)`;
- registry lookup by string or type;
- dependency-injection selection;
- a route defined through a configuration table;
- import aliases and re-export chains;
- an HTTP client call matching a route in another repository;
- a plugin selected by an entry-point name.

These are better represented as guarded inference rules:

\[
P_1 \land P_2 \land \cdots \land P_n \Rightarrow Q
\]

The resulting analysis is a least-fixed-point computation over facts and
abstract values. The existing graph becomes one input to the solver rather than
the complete semantic model.

## 4. Formal model

### 4.1 Program universe

For a scanned workspace define:

- \(S\): symbols;
- \(M\): modules/files;
- \(R \subseteq S \cup M\): declared and discovered roots;
- \(F\): extracted semantic facts;
- \(C\): conservative cautions representing unsupported or open-world behavior;
- \(A\): abstract values associated with expressions and bindings.

Facts include relations such as:

```text
defines(module, symbol)
imports(source, target)
calls(source, target)
inherits(child, parent)
reads_attribute(source, receiver, name_value)
registers(registry, key_value, target)
exports(module, symbol)
route(path_value, method_value, handler)
declared_entrypoint(symbol)
runtime_observed(symbol)
```

### 4.2 Abstract value domains

The first implementation should support small, finite domains that solve the
highest-value dynamic patterns:

```text
StringValue = Bottom | FiniteSet[str] | Prefix[str] | Unknown
TypeValue   = Bottom | FiniteSet[type-symbol] | Unknown
ModuleValue = Bottom | FiniteSet[module] | Unknown
CallableValue = Bottom | FiniteSet[symbol] | Unknown
BoolValue   = Bottom | True | False | Unknown
```

Each domain forms a lattice with a join operation \(\sqcup\). For example:

\[
\{\text{"run"}\} \sqcup \{\text{"stop"}\}
= \{\text{"run"}, \text{"stop"}\}
\]

When a finite set exceeds a configured bound, widening promotes it to
`Unknown`. Widening may reduce precision but must never remove a possible
value.

### 4.3 Liveness lattice

Every symbol has one internal analysis state:

```text
UNREACHED < MAYBE_LIVE < PROVEN_LIVE
```

- `PROVEN_LIVE`: reached through a known root and resolved semantic rules;
- `MAYBE_LIVE`: a feasible path exists but depends on an unknown/open-world
  value or unsupported semantic feature;
- `UNREACHED`: no derivation exists after fixed-point convergence.

Public statuses are derived conservatively:

| Internal state and exposure | Public status |
|---|---|
| `PROVEN_LIVE` | `definitely_used` |
| `MAYBE_LIVE` | `uncertain_dynamic_risk` |
| `UNREACHED` but externally exposed/test-only/open-world | `likely_dead` |
| `UNREACHED`, closed, and all safety gates pass | `safe_to_delete` |

### 4.4 Inference rules

Representative rules:

\[
\text{Root}(s) \Rightarrow \text{ProvenLive}(s)
\]

\[
\text{ProvenLive}(f) \land \text{DirectCall}(f,g)
\Rightarrow \text{ProvenLive}(g)
\]

\[
\text{MayLive}(f) \land \text{DirectCall}(f,g)
\Rightarrow \text{MayLive}(g)
\]

\[
\text{Live}(f) \land \text{GetAttr}(f,o,x) \land
\text{NameMayResolve}(o,x,g) \Rightarrow \text{MayLive}(g)
\]

\[
\text{Live}(f) \land \text{RegistryLookup}(f,r,k) \land
\text{RegistryBinding}(r,k,g) \Rightarrow \text{MayLive}(g)
\]

A rule upgrades a target to `PROVEN_LIVE` only when all required values and
bindings are exact. If a premise contains `Unknown`, the result is at most
`MAYBE_LIVE`.

### 4.5 Fixed-point solution

Start from extracted facts, roots, and bottom abstract values:

\[
X_0 = F \cup R
\]

Apply the monotone transfer function \(T\):

\[
X_{n+1} = T(X_n)
\]

Stop when:

\[
X_{n+1} = X_n = X^*
\]

For finite-height domains, or domains made finite through widening, convergence
is guaranteed. Worklist evaluation should make runtime approximately
proportional to the number of facts actually changed rather than repeatedly
scanning every rule.

### 4.6 Safe verdict predicate

`safe_to_delete(s)` requires every gate below:

\[
\begin{aligned}
\text{Safe}(s) \iff {} & \text{State}(s)=\text{UNREACHED} \\
& \land \neg\text{ExternalExposure}(s) \\
& \land \neg\text{OpenWorldCaution}(s) \\
& \land \neg\text{UnresolvedDynamicTarget}(s) \\
& \land \text{TextualBackstopPasses}(s) \\
& \land \text{ModelCoveragePasses}(s)
\end{aligned}
\]

The `ModelCoveragePasses` gate is new. It records whether every syntax and
semantic feature capable of targeting the symbol's namespace was either
analyzed or conservatively poisoned.

## 5. Constraint solving and SMT

### 5.1 Start without a mandatory SMT dependency

Most useful cases can be solved with finite-set abstract interpretation:

```python
name = "run"
getattr(worker, name)()
```

Here `name` evaluates to `FiniteSet{"run"}`, allowing exact target resolution.
Constant concatenation, simple format strings, dictionary lookups, and branch
joins should also use the finite domain.

### 5.2 Optional SMT tier

Introduce an optional solver only for constraints the built-in domain cannot
decide, especially string and configuration predicates:

\[
\Phi \land (x = \text{"handler_name"})
\]

- `SAT`: the target is feasible and must be live/maybe-live;
- `UNSAT`: that candidate path is impossible and may be excluded;
- `UNKNOWN` or timeout: preserve uncertainty.

An SMT timeout must never permit `safe_to_delete`. The package should remain
useful without the optional dependency, with lower precision but identical
safety behavior.

### 5.3 Solver scope limits

Initial SMT support should be restricted to:

- equality and inequality over finite strings;
- prefix/suffix/contains constraints;
- finite enum/config choices;
- simple boolean guards;
- type membership from a finite extracted set.

Do not attempt general symbolic execution of Python or JavaScript in the first
version.

## 6. Proof certificates

Every verdict should carry a machine-readable certificate. The certificate is
an explanation trace, not a claim about behavior outside the model.

For live symbols, record:

- root that initiated reachability;
- ordered inference-rule chain;
- concrete edges and source locations;
- abstract values used in target resolution;
- runtime observations, if any.

For deletion candidates, record:

- fixed-point engine/model version;
- repository fingerprint and configuration hash;
- symbol identity and source span;
- root set used;
- namespaces and dynamic sites checked;
- unresolved cautions (must be empty for `safe_to_delete`);
- textual-backstop result;
- model-coverage result;
- reason no inference rule derives liveness;
- deletion plan generated by the existing advisory layer.

Suggested schema:

```json
{
  "engine": "fixed-point-v1",
  "model_version": "python-constraints-v1",
  "symbol": "pkg.mod:legacy",
  "state": "UNREACHED",
  "roots_hash": "...",
  "facts_hash": "...",
  "dynamic_sites_checked": [],
  "open_world_cautions": [],
  "backstop": "pass",
  "coverage": "pass",
  "verdict": "safe_to_delete"
}
```

Certificates should be deterministic so CI can diff them across commits.

## 7. Architecture changes

### 7.1 New core modules

```text
codetruth/core/facts.py          normalized semantic facts
codetruth/core/domains.py        abstract value lattices and widening
codetruth/core/constraints.py    guarded rule representation
codetruth/core/solver.py         monotone worklist/fixed-point engine
codetruth/core/certificate.py    derivation and safety certificates
codetruth/core/coverage.py       modeled/unsupported feature accounting
```

Optional later module:

```text
codetruth/core/smt.py            solver-neutral interface and optional backend
```

### 7.2 Existing modules to evolve

- `codetruth/core/models.py`: add facts, abstract values, derivations, coverage
  records, and certificate references while preserving serialized compatibility.
- `codetruth/core/graph.py`: remain the concrete edge store and expose graph
  edges as normalized facts.
- `codetruth/core/evidence.py`: derive public statuses from solver state plus
  existing policy gates; retain ranking as review ordering only.
- `codetruth/core/scanner.py`: orchestrate extraction, solving, backstop,
  certification, reporting, and caching.
- `codetruth/core/cache.py`: include engine/model version, facts, configuration,
  and solver options in cache invalidation.
- `codetruth/core/plugin.py`: add hooks for fact extraction, abstract transfer
  functions, and model-coverage reporting.
- Python and JavaScript language plugins: emit facts rather than encoding every
  dynamic behavior directly as weak graph edges.

### 7.3 Compatibility strategy

During migration, every existing `Edge` is translated to a fact:

```text
STRONG edge -> exact propagation rule
WEAK edge   -> maybe-live propagation rule with its current evidence
```

The old evidence engine remains available behind an internal feature flag until
parity and safety validation are complete.

## 8. Implementation phases

### Phase 0 — Specification and golden corpus (2–4 days)

- Freeze current outputs for all fixtures and real validation labels.
- Write precise definitions for roots, external exposure, dynamic poisoning,
  model coverage, and each public status.
- Create adversarial fixtures for aliases, dynamic names, registries, decorators,
  imports, re-exports, monkeypatching, and unknown input.
- Define certificate JSON schema and deterministic ordering.

Exit criteria:

- current behavior is captured in golden files;
- no ambiguity remains about when uncertainty blocks safety;
- every existing safe fixture has a documented reason.

### Phase 1 — Fact model and compatibility solver (4–7 days)

- Add normalized fact types.
- Translate existing symbols, edges, markers, and cautions into facts.
- Implement monotone worklist propagation for exact and maybe-live edges.
- Produce derivation chains for all live/maybe-live states.
- Keep current public evidence/status logic as the final policy layer.

Exit criteria:

- new engine matches or is more conservative than current statuses;
- no current `definitely_used` symbol becomes `safe_to_delete`;
- all tests pass with both engines;
- solver output is deterministic.

### Phase 2 — Abstract strings and callable resolution (1–2 weeks)

- Implement finite string, module, type, and callable domains.
- Support constants, assignments, aliases, branch joins, simple concatenation,
  dictionary literals, and bounded format strings.
- Resolve `getattr`, registries, import-by-name, and callable collections when
  values are finite.
- Widen large/recursive sets to `Unknown`.
- Add model-coverage reporting for unsupported expressions.

Exit criteria:

- exact dynamic fixtures move from weak uncertainty to precise liveness;
- unknown dynamic fixtures remain uncertain;
- no unsafe precision gain appears in mutation/adversarial tests.

### Phase 3 — Framework constraints (1–2 weeks)

- Convert high-value Python rules into guarded facts: FastAPI/Starlette routes,
  Django URL/signal wiring, Celery tasks, Click/Typer commands, pytest hooks,
  SQLAlchemy registrations, and Pydantic validators.
- Convert JavaScript framework support for Express/Fastify, React/Vue entry
  conventions, package exports, and workspace aliases.
- Attach every framework inference to a rule identifier and source location.

Exit criteria:

- framework behavior is represented as auditable derivations;
- YAML rules can emit normalized facts without core-code changes;
- existing framework tests retain or improve conservatism.

### Phase 4 — Certificates and independent verifier (1 week)

- Emit certificates for every result.
- Build a small independent certificate checker that validates hashes, required
  gates, derivation references, and absence of unresolved cautions.
- Add `codetruth check-certificate <file>`.
- Add certificate output to CLI JSON, HTML, Python API, and MCP.

Exit criteria:

- corrupted or incomplete certificates fail closed;
- certificates are reproducible across identical scans;
- a reviewer can trace each verdict to source facts and policy gates.

### Phase 5 — Optional SMT refinement (1–2 weeks)

- Define a solver-neutral API.
- Add optional string/boolean constraint backend.
- Enforce time and memory budgets.
- Treat timeout/error/unknown as uncertainty.
- Record query, result, limits, and solver version in certificates.

Exit criteria:

- SMT can remove demonstrably impossible dynamic targets;
- disabling SMT never produces fewer cautions than enabling it;
- timeout tests prove fail-closed behavior.

### Phase 6 — Real-repository validation and rollout (ongoing, minimum 2 weeks)

- Validate on at least five Python and three JavaScript/TypeScript repositories.
- Hand-label at least 50 symbols per major ecosystem.
- Compare old and new engines on every changed verdict.
- Manually audit every new `safe_to_delete` result.
- Ship initially as `--engine constraints-preview`, then make it default only
  after the safety gate is met.

Exit criteria:

- zero audited false-safe verdicts;
- documented precision/recall changes;
- acceptable runtime and memory overhead;
- migration and limitation documentation published.

## 9. Testing strategy

### 9.1 Unit and algebraic tests

For every abstract domain test:

- join is commutative, associative, and idempotent;
- transfer functions are monotone;
- widening never removes a concrete possibility;
- serialization round-trips deterministically.

Property checks:

\[
x \sqsubseteq x \sqcup y
\]

\[
x \sqsubseteq y \Rightarrow T(x) \sqsubseteq T(y)
\]

### 9.2 Differential safety tests

Run old and new engines together. Any transition into `safe_to_delete` requires
an explicit golden approval and certificate audit. Automatically fail CI on:

```text
old definitely_used -> new safe_to_delete
old uncertain_dynamic_risk -> new safe_to_delete without a proved UNSAT guard
coverage incomplete -> safe_to_delete
solver timeout/error -> safe_to_delete
```

### 9.3 Metamorphic tests

Apply transformations that should not change semantics:

- rename a local alias;
- reorder independent definitions;
- replace a literal with an equivalent constant;
- add an intermediate import/re-export;
- wrap a direct registration in an equivalent table;
- split or merge modules without changing exports.

Verdicts should remain equivalent or become more conservative, never unsafe.

### 9.4 Mutation tests

Inject hidden usage paths into dead fixtures:

- direct call;
- aliased import;
- string registry;
- route decorator;
- `getattr` with exact and unknown names;
- configuration mapping;
- public export;
- runtime observation.

Every mutation must prevent `safe_to_delete`.

### 9.5 Real-world audits

Maintain label files with:

- symbol and commit hash;
- human label;
- evidence used for labeling;
- external/config/runtime assumptions;
- CodeTruth status and certificate;
- adjudication notes for disagreements.

## 10. Evaluation metrics

Safety metrics:

- audited false-safe count: must remain zero;
- unsafe transition count in differential tests: zero;
- percentage of safe verdicts with complete certificates: 100%;
- percentage of unsupported features that fail closed: 100%.

Usefulness metrics:

- recall among hand-labeled dead symbols;
- precision of the review queue;
- number of uncertain results resolved by abstract values;
- number of impossible dynamic targets eliminated by constraints;
- certificate explanation length and reviewer comprehension.

Performance metrics:

- extraction time;
- solver time;
- facts and derivations per symbol;
- peak memory;
- cache hit time;
- optional SMT queries, timeouts, and median latency.

Suggested release budgets for the first default version:

```text
zero audited false-safe verdicts
<= 2x current uncached scan time on validation repos
<= 2.5x current peak memory
100% deterministic certificates on repeated scans
```

## 11. Risks and mitigations

### State explosion

Finite-set values can grow rapidly. Bound sets, widen to `Unknown`, cache joins,
and propagate only changed facts. Precision may degrade; safety must not.

### False confidence from formal language

Certificates prove a result only relative to the model and repository boundary.
Every report must state model version, assumptions, unsupported features, and
open-world cautions.

### SMT complexity and instability

Keep SMT optional, narrow, resource-bounded, and fail-closed. Never let solver
availability alter the safety policy.

### Framework model drift

Version framework rules, record which rules fired, add fixtures from real bugs,
and allow explicit user entrypoints/configuration to override missing knowledge
conservatively.

### Cache unsoundness

Invalidate on source, config, plugin, rule-pack, engine, domain, solver, and
certificate-schema versions. Never incrementally patch liveness unless all
affected dependencies are proven complete.

### Open-world usage

Public libraries, reflection-heavy modules, external services, plugin systems,
and generated code remain cautious unless the user supplies a closed-world
boundary or evidence. Runtime non-observation can improve ranking but cannot by
itself prove unreachability.

## 12. User-facing changes

CLI additions proposed:

```text
codetruth scan REPO --engine constraints-preview
codetruth scan REPO --certificate-dir certificates/
codetruth check REPO SYMBOL --explain-derivation
codetruth check-certificate certificate.json
codetruth scan REPO --solver none|auto|smt
```

Evidence should answer:

1. What roots were assumed?
2. Why is this symbol live, possibly live, or unreachable?
3. Which dynamic sites could target it?
4. Which model features were unsupported?
5. What would change the verdict?
6. Can an independent checker validate the certificate?

No existing API field should be removed during the preview period.

## 13. Recommended first implementation slice

The first mergeable slice should avoid SMT and framework rewrites. Build only:

1. normalized facts for existing strong/weak edges and roots;
2. the three-state liveness lattice;
3. a deterministic worklist fixed-point solver;
4. derivation traces;
5. a compatibility adapter back into `EvidenceRecord`;
6. differential tests against the current engine.

This establishes the mathematical core with low migration risk. Abstract
strings and guarded dynamic resolution can then be added one feature at a time,
with every increase in precision visible as a reviewed verdict transition.

## 14. Definition of success

The mathematical engine is successful when CodeTruth can make this precise
claim:

> For every `safe_to_delete` verdict, CodeTruth computed the least fixed point
> of its versioned conservative semantic model, found no feasible derivation
> from any declared root to the symbol, encountered no unresolved dynamic or
> open-world condition capable of targeting it, passed an independent textual
> audit, and emitted a deterministic certificate containing those facts.

This does not solve undecidability or external unknowns. It turns those limits
into explicit uncertainty while making all supported reasoning systematic,
composable, testable, and auditable.
