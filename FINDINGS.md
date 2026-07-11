# Real dead code CodeTruth found

Every claim below is reproducible: `pip install codetruth`, run the command,
read the evidence. All findings were verified by hand (grep + reading the
source) before being listed. The tool is advisory — it found these; humans
confirmed them.

## In shipped, mature libraries

Found while running the false-positive audit across 10 real packages
(36k+ unique symbols, **0 false positives** — see
[validation/VALIDATION.md](validation/VALIDATION.md)):

| Package | Symbol | What it is |
|---|---|---|
| urllib3 | `connectionpool._url_from_pool` | helper defined once, called nowhere |
| rich | `console._svg_hash` | SVG export helper, never invoked |
| rich | `_export_format._SVG_FONT_FAMILY`, `_SVG_CLASSES_PREFIX` | constants no code reads |
| requests | `utils.dict_to_sequence` | util with zero references in the package |
| jinja2 | `meta._RefType` | type alias with no remaining use |

```console
$ codetruth check ./urllib3 connectionpool:_url_from_pool
"status": "safe_to_delete",
"evidence_for_deletion": [
  "No strong references (calls/imports/inheritance) found in the repository",
  "No string-literal, reflection, or attribute-name references detected",
  "Not matched by any framework/entry-point rule",
  "Not referenced by the test suite",
  "Verified: symbol name occurs nowhere else in the repository's text"
]
```

## In real application code (author's own repos)

- **async-fastapi**: `statusResponse` — a pydantic response model defined and
  never wired to any endpoint; `JobManager.delete_job` — a storage method
  with no delete route.
- **DualRAG** (1,250-symbol RAG chatbot): `ReportMetrics` — an entire
  dataclass plus all eight fields, referenced nowhere; ~100 further
  unwired chains, prompt builders and template formatters surfaced for
  review.

## What it correctly *refused* to flag

The interesting half. On the same codebases, symbols with **zero direct
references** that a grep-based cleanup would have deleted:

- FastAPI/Flask/Django route handlers, Celery tasks, pytest fixtures —
  invoked by frameworks, never called by name.
- pydantic `@field_validator` / `@model_validator` methods — run by the
  validation machinery.
- **Enum members** like `ChartType.LINE = "line"` — constructed *by value*
  when pydantic coerces incoming `"line"` strings. The member's name appears
  nowhere in the repo; deleting it breaks parsing. No name-based tool can see
  this, which is exactly why CodeTruth caps enum members at review tier
  instead of calling them safe.
- `TextWrapper._handle_long_word` (click) — an override whose only caller is
  the *stdlib parent class*.
- `Cycler.reset` (jinja2) — called only from inside user templates.
- `babel_extract` (jinja2) — consumed by Babel through `entry_points`
  metadata, not by any import.

## The methodology, in one paragraph

CodeTruth never asks "does this look unused?" — it asks **"can we prove it's
used?"** and only says `safe_to_delete` when every analysis fails to find a
usage path *and* the symbol's name appears nowhere else in the repository's
text. Everything with weak evidence (string references, reflection targets,
framework wiring, public API surface) lands in graded review tiers with the
evidence attached. False positives are the only fatal failure for a deletion
tool, so the design trades recall for the guarantee — and the measured result
across 36k symbols of real code is zero.
