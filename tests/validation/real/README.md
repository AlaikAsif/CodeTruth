# Real-repo validation labels (PLAN §9 / ROADMAP 1.2.2)

Hand-verified ground truth for installed third-party packages. Each label was
established by grep over the package source ("hand-verification"): a symbol is
`used` when a non-definition reference exists (call site, registry/dispatch
entry, decorator argument, re-export) or when its consumer is *outside* the
repo by design; `dead` only when the sole occurrence in the package is its own
definition.

Run with:

    python scripts/measure_recall.py tests/validation/real/jinja2.labels.json \
                                     tests/validation/real/click.labels.json

Paths inside the label files point at this machine's site-packages — adjust
`repo` when regenerating elsewhere.

## Why the trap rows matter

Rows whose only usage is outside the repository are the false-positive
guard — the tool must keep them out of `safe_to_delete` with zero in-repo
call sites to go on:

| Symbol | Outside consumer |
|---|---|
| `utils:Cycler.reset` (jinja2) | called from inside templates by end users |
| `ext:babel_extract` (jinja2) | Babel, via `entry_points` metadata |
| `_textwrap:TextWrapper._handle_long_word` (click) | the stdlib `textwrap` parent class |

## Status

Starter set: jinja2 (15 symbols, 1 verified-dead) and click (4 symbols).
Genuinely dead code is rare in mature released libraries — most rows guard
the FP boundary rather than measure recall. Growing this to ~50 symbols per
repo across 5 repos (plus an error analysis) is the remaining 1.2.2 work.
