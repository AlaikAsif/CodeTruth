"""Scan progress reporting.

A scan of a big repo can take tens of seconds; without feedback it reads as
a hang. `scan(..., progress=cb)` invokes the callback as work advances:

    cb(phase: str, done: int, total: int, detail: str)

- phase:  "extract" (per-file), then "edges", "rules", "classify", "verify"
- done/total: file counts during extract; 0/0 for single-shot phases
- detail: the current file (extract) or "" otherwise

`ProgressRenderer` is the CLI's implementation: a single self-overwriting
line on stderr, throttled so rendering never becomes the bottleneck, and
auto-disabled when stderr isn't a TTY (CI logs stay clean). Cancellation is
plain Ctrl+C — the CLI catches KeyboardInterrupt and exits 130 cleanly.
"""
from __future__ import annotations

import sys
import time


class ProgressRenderer:
    """Single-line stderr progress: throttled, self-clearing, TTY-aware."""

    def __init__(self, stream=None, min_interval: float = 0.1):
        self.stream = stream if stream is not None else sys.stderr
        self.min_interval = min_interval
        self._last = 0.0
        self._width = 0
        self._active = False

    def __call__(self, phase: str, done: int, total: int, detail: str = "") -> None:
        now = time.monotonic()
        # Always render phase transitions and the final file; throttle the rest.
        is_edge = done == total or done <= 1
        if not is_edge and now - self._last < self.min_interval:
            return
        self._last = now
        if phase == "extract" and total:
            line = f"scanning {done}/{total} files"
            if detail:
                line += f" — {detail}"
        else:
            labels = {"extract": "discovering files", "edges": "building graph",
                      "rules": "applying rules", "classify": "classifying",
                      "verify": "verifying safe verdicts"}
            line = labels.get(phase, phase) + "…"
        self._write(line)
        self._active = True

    def _write(self, line: str) -> None:
        if len(line) > 100:
            line = line[:97] + "..."
        pad = max(0, self._width - len(line))
        self.stream.write("\r" + line + " " * pad)
        self.stream.flush()
        self._width = len(line)

    def close(self) -> None:
        """Erase the progress line so real output starts on a clean row."""
        if self._active:
            self.stream.write("\r" + " " * self._width + "\r")
            self.stream.flush()
            self._active = False
