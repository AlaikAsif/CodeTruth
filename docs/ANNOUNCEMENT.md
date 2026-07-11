# Announcement drafts (post these yourself when ready)

Ready-to-paste copy for launching CodeTruth. Tweak voice as you like — the
claims are all backed by `validation/` and `FINDINGS.md`, so keep them exact.

---

## Show HN draft

**Title:**
Show HN: CodeTruth – lets AI agents delete dead code without breaking prod

**Text:**

AI agents are decent at writing code and terrible at deleting it — they
hallucinate "this looks unused" from a partial view of the repo. I built
CodeTruth to invert the question: instead of *"is this used?"* it asks
*"can we prove it's used?"* and only marks a symbol `safe_to_delete` when
every analysis fails to find a usage path AND the name appears nowhere else
in the repo's text.

It's a static-analysis engine (Python + JS/TS) with framework awareness
(FastAPI/Django/Express routes, pydantic validators, Celery, React/JSX,
enum-by-value construction...), a strong/weak evidence graph, and a 4-way
verdict with the evidence attached to every symbol. Agents consume it as an
MCP server; humans get a CLI, CI gate with baselines, and HTML reports. It's
advisory by design — it never edits code.

Measured across 10 real packages (requests, flask, sqlalchemy, networkx…;
36k+ symbols): **zero false positives**, while still finding genuinely dead
code in urllib3, rich, requests and jinja2 (list + evidence in FINDINGS.md).
The interesting engineering is the refusals: enum members constructed by
value (`ChartType("line")`), stdlib-override methods, template-only helpers —
all zero-reference and all load-bearing.

pip install codetruth · MIT · https://github.com/AlaikAsif/CodeTruth

I'd genuinely like to see repos where it gets a verdict wrong — the
false-positive audit is reproducible with one script.

---

## MCP directory listing (mcp.so / PulseMCP / Smithery / awesome-mcp-servers)

**Name:** CodeTruth

**One-liner:** Deletion-safety verification for code — evidence-graded dead
code detection agents can act on without breaking production.

**Description:**
Before your agent deletes "unused" code, have it ask CodeTruth. Four tools:
`check_deletion_safety(repo, symbol)` returns a 4-way verdict
(safe_to_delete / likely_dead / uncertain_dynamic_risk / definitely_used)
with evidence for and against; `scan(repo)` returns the ranked review queue;
`plan_deletion` describes the exact removal span and newly-orphaned imports;
`scan_workspace` links usage across repos (HTTP route ↔ client call, shared
imports). Detection is deterministic and framework-aware (Python + JS/TS);
verified at 0 false positives across 36k symbols of real code. Advisory
only — it never modifies files.

**Install:**
```
pip install "codetruth[mcp]"
claude mcp add codetruth -- codetruth mcp
```

---

## r/Python draft

**Title:** CodeTruth: dead-code detection that's safe enough for AI agents
to act on (0 false positives across 36k symbols)

**Text:** Same body as Show HN, swap the last line for: "It's on PyPI as
`codetruth`; the CLI works standalone (`codetruth scan .` / `--ci` with a
baseline file, like mypy's) if you don't care about the agent part."

---

## Where to submit (owner actions)

1. **Hacker News** — Show HN, weekday morning US time works best.
2. **MCP directories** — mcp.so, pulsemcp.com, smithery.ai, and a PR to
   `punkpeye/awesome-mcp-servers` (listing text above).
3. **r/Python** — Sunday "what are you working on" threads are friendly.
4. Optional: a short post/thread with the FINDINGS.md table — "dead code we
   found in urllib3/rich/requests" travels well on its own.
