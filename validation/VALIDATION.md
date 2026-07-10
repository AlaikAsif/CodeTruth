# CodeTruth — Validation

The narrative companion to the auto-generated [REPORT.md](REPORT.md) (raw
numbers) and [calibration.json](calibration.json). Reproduce with:

```bash
python scripts/validation_report.py <repos...>   # FP audit + features
python scripts/measure_recall.py    <labels...>  # recall vs ground truth
python scripts/calibrate.py                       # empirical P(dead)
```

## What we measure, and why

CodeTruth exists to let agents delete code without breaking production, so the
only failure that matters is a **false positive** — a symbol called
`safe_to_delete` that is actually used. Recall (how much genuinely-dead code we
surface) is secondary: a tool that misses dead code is merely unhelpful; a tool
that greenlights a live deletion is dangerous. We therefore report the two
separately and hold false positives to zero.

## 1. False-positive audit at scale

`scripts/validation_report.py` scans each repo in library mode (the
conservative setting) and, for every `safe_to_delete` verdict, independently
re-checks with a whole-repo regex that the symbol's name occurs nowhere
outside its own definition file. Any occurrence is counted a false positive.

**Result (see REPORT.md for the table): 0 false positives across 10 real
packages, 36,457 symbols, 53 `safe_to_delete` verdicts.** Packages span
requests, flask, click, jinja2, werkzeug, rich, pydantic, urllib3, sqlalchemy
(17k symbols, 286k edges), and networkx.

Spot-checks confirm the safe verdicts are genuine dead code, not audit gaps:

- `urllib3 connectionpool:_url_from_pool` — defined once, called nowhere.
- `rich console:_svg_hash` — defined once, never invoked.
- `rich _export_format:_SVG_FONT_FAMILY` — module constant, never referenced.
- `jinja2 meta:_RefType` — type alias with no remaining use.

These are real findings: CodeTruth located dead code in mature, released
libraries while keeping false positives at zero.

## 2. Recall (ground truth by construction + hand labels)

Genuinely-dead code is rare in shipped libraries, so recall is measured where
ground truth is known: the constructed application in `tests/validation/`
(20 symbols, dead/used by construction) and grep-verified samples of jinja2
and click in `tests/validation/real/`.

**Aggregate across the constructed app + grep-verified jinja2/click/requests
labels (8 genuinely-dead symbols):**

- **0 false positives** (no used symbol ranked `safe_to_delete`);
- **recall@queue 8/8 (100%)** — every dead symbol is surfaced for review;
- **recall@safe 6/8 (75%)** — three-quarters reach the actionable
  `safe_to_delete` tier; the other two are dead *public API* correctly capped
  at `likely_dead` (a library's external callers are invisible);
- **0 stuck** — no dead symbol hides as `definitely_used`.

The constructed app alone is 6/6 in-queue with 5/6 safe (the sixth a
dead-cluster interior, surfaced as `likely_dead` until its dead caller goes).

## 3. Calibration

`scripts/calibrate.py` joins all labels to their verdicts and reports the
empirical P(dead) per status tier and per `rank_score` bucket (table in
REPORT.md). From 51 labeled symbols the tiers come out cleanly monotone:

| status | P(dead) |
|---|--:|
| `safe_to_delete` | 1.00 (6/6) |
| `likely_dead` | 0.22 (2/9) |
| `uncertain_dynamic_risk` | 0.00 (0/8) |
| `definitely_used` | 0.00 (0/28) |

Two results worth calling out. First, `safe_to_delete` is **100% dead** — the
guarantee holds on labeled data, not just the FP audit. Second, `uncertain`
(weak usage evidence) is *less* dead than `likely_dead` (no evidence at all),
which is why the tool hedges `uncertain` hardest — and why `likely_dead` is
never auto-deletable: 78% of labeled `likely_dead` symbols turned out to be
used public API. The `rank_score` buckets are monotone too (0.0 → P=0.00,
0.5–0.8 → 0.22, 0.8–1.0 → 1.00).

`rank_score` remains a **documented heuristic, not a shipped probability**
(PLAN §4). The label set (tens of symbols) is enough to *validate* that the
ordering is monotone and sane, not to *fit* a precise calibrated float; we
will not print a probability the data can't back. Growing the middle-tier
label set is the path to a genuinely calibrated confidence, and the harness
is now in place to do it.

## 4. Error analysis — where recall leaks

The tool is deliberately conservative, so its errors are almost entirely
**false negatives** (dead code kept in a review tier), never false positives.
The recurring reasons a genuinely-dead symbol fails to reach `safe_to_delete`:

1. **Public API surface** — an exported/public symbol with no in-repo caller
   is capped at `likely_dead`, because a library's external consumers are
   invisible. This is correct for libraries and the dominant reason mature
   packages show large `likely_dead` counts; `--app-mode` / `.codetruth.toml`
   `app_mode` unlocks `safe_to_delete` for application code.
2. **Dead clusters** — a dead function reached only by other dead code shows
   as `likely_dead` (with cluster evidence) until its caller is removed; a
   second pass then clears the interior.
3. **Dynamic-module poisoning** — any non-literal `getattr`/`eval` caps every
   symbol in that module at `uncertain_dynamic_risk`. Conservative by design;
   the cost is recall in reflection-heavy modules.
4. **Weak name-match fan-out** — a private helper sharing a common name with
   an attribute access elsewhere collects a weak edge and lands in
   `uncertain`. The `rank_score` weighting pushes these to the top of the
   review queue so they are cheap to clear by eye.

None of these produce a wrong deletion; each trades recall for the guarantee.

## 5. Limitations

- Cross-service usage is only visible with `codetruth workspace` (and, fully,
  with runtime tracing) — single-repo scans assume the repo is the world.
- Recall numbers rest on a modest hand-labeled set; the FP number rests on
  36k+ symbols and is the robust one.
- JavaScript validation is smoke-level (jupyterlab, 0 unsafe) plus fixtures;
  a hand-labeled JS recall run is future work.
