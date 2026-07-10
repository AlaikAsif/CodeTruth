# CodeTruth validation report

Generated 2026-07-10 by `scripts/validation_report.py`. Library mode (`treat_public_as_api=True`, conservative).

## False-positive audit (the metric that matters)

**0 false positive(s)** across 10 repos, 36457 symbols, 53 `safe_to_delete` verdicts. A false positive = a symbol marked `safe_to_delete` whose name occurs elsewhere in the repo (i.e. possibly used).

| repo | symbols | edges | scan (s) | safe | likely_dead | uncertain | used | FP |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| requests | 352 | 523 | 0.3 | 0 | 87 | 62 | 203 | 0 |
| flask | 588 | 1407 | 0.3 | 0 | 103 | 194 | 291 | 0 |
| click | 713 | 2725 | 0.5 | 0 | 102 | 196 | 415 | 0 |
| jinja2 | 1335 | 6793 | 0.6 | 1 | 86 | 524 | 724 | 0 |
| werkzeug | 1631 | 5848 | 0.9 | 0 | 177 | 413 | 1041 | 0 |
| rich | 1654 | 10268 | 1.4 | 3 | 179 | 403 | 1069 | 0 |
| pydantic | 3522 | 19681 | 2.3 | 0 | 138 | 1372 | 2012 | 0 |
| urllib3 | 928 | 2576 | 0.9 | 4 | 144 | 258 | 522 | 0 |
| sqlalchemy | 17341 | 286162 | 29.4 | 45 | 990 | 7713 | 8593 | 0 |
| networkx | 8393 | 117010 | 10.9 | 0 | 1164 | 1193 | 6036 | 0 |

**Totals:** 36457 symbols, 53 safe_to_delete, **0 false positives.**

No false positives: every `safe_to_delete` verdict names a symbol that appears nowhere else in its repository.


## Calibration (empirical P(dead) from labels)

From 51 hand/construction-labeled symbols (tests/validation). P(dead) = fraction of labeled symbols in the bucket that are genuinely dead. Small N = coarse estimate.

### By status tier

| status | labeled | dead | P(dead) |
|---|--:|--:|--:|
| safe_to_delete | 6 | 6 | 1.00 |
| likely_dead | 9 | 2 | 0.22 |
| uncertain_dynamic_risk | 8 | 0 | 0.00 |
| definitely_used | 28 | 0 | 0.00 |

### By rank_score bucket (isotonic view)

| rank_score | labeled | dead | P(dead) |
|---|--:|--:|--:|
| [0.0,0.1) | 28 | 0 | 0.00 |
| [0.1,0.3) | 1 | 0 | 0.00 |
| [0.3,0.5) | 7 | 0 | 0.00 |
| [0.5,0.8) | 9 | 2 | 0.22 |
| [0.8,1.0) | 6 | 6 | 1.00 |

Interpretation: the tiers are monotone in P(dead) — `definitely_used` ~0, `safe_to_delete` ~1 — which is exactly what a deletion-safety tool must show. The middle tiers carry the uncertainty by design. Expanding the labeled set (esp. the middle tiers) tightens these estimates.

