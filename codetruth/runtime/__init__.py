"""Phase 6 — runtime usage evidence (v1.5).

`@codetruth.track` records real invocations of a function in a running app.
"Zero calls observed over N days" is the strongest evidence for deletion —
and the only answer to cross-service usage that static analysis can't see.

Log format: JSON lines in the file named by $CODETRUTH_RUNTIME_LOG
(default: .codetruth/runtime.jsonl in the CWD).
  {"event": "register", "symbol": "pkg.mod:func", "ts": 1710000000.0}
  {"event": "calls", "symbol": "pkg.mod:func", "count": 17, "ts": ...}
"""
from __future__ import annotations

import atexit
import functools
import json
import os
import threading
import time
from collections import Counter
from pathlib import Path

from ..core.models import Marker, MarkerKind

_lock = threading.Lock()
_counts: Counter = Counter()
_registered: set[str] = set()
_flusher_installed = False


def _log_path() -> Path:
    return Path(os.environ.get("CODETRUTH_RUNTIME_LOG",
                               ".codetruth/runtime.jsonl"))


def _append(entry: dict) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _flush() -> None:
    with _lock:
        counts, _counts_copy = dict(_counts), _counts.clear()
    ts = time.time()
    for symbol, count in counts.items():
        _append({"event": "calls", "symbol": symbol, "count": count, "ts": ts})


def _install_flusher() -> None:
    global _flusher_installed
    if not _flusher_installed:
        atexit.register(_flush)
        _flusher_installed = True


def track(fn=None, *, name: str | None = None):
    """Decorator: register the symbol at import, count real invocations.

    Usage:  @codetruth.track          or   @codetruth.track(name="pkg.mod:func")
    """
    def decorate(func):
        symbol = name or f"{func.__module__}:{func.__qualname__}"
        with _lock:
            if symbol not in _registered:
                _registered.add(symbol)
                _append({"event": "register", "symbol": symbol,
                         "ts": time.time()})
        _install_flusher()

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with _lock:
                _counts[symbol] += 1
                total = sum(_counts.values())
            if total >= 1000:  # bound memory between flushes
                _flush()
            return func(*args, **kwargs)
        return wrapper

    return decorate(fn) if fn is not None else decorate


def load_runtime_log(path: str | Path) -> dict[str, dict]:
    """Aggregate a runtime log: symbol -> {calls, registered_ts, last_ts}."""
    out: dict[str, dict] = {}
    p = Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        sym = entry.get("symbol")
        if not sym:
            continue
        rec = out.setdefault(sym, {"calls": 0, "registered_ts": None,
                                   "last_ts": None})
        if entry.get("event") == "register":
            ts = entry.get("ts")
            if rec["registered_ts"] is None or (ts and ts < rec["registered_ts"]):
                rec["registered_ts"] = ts
        elif entry.get("event") == "calls":
            rec["calls"] += int(entry.get("count", 0))
            rec["last_ts"] = entry.get("ts") or rec["last_ts"]
    return out


def load_runtime_markers(path: str | Path,
                         known_symbols: set[str]) -> list[Marker]:
    """Convert a runtime log into evidence markers for the classifier."""
    markers: list[Marker] = []
    now = time.time()
    for symbol, rec in load_runtime_log(path).items():
        if symbol not in known_symbols:
            continue
        if rec["calls"] > 0:
            markers.append(Marker(
                symbol, MarkerKind.RUNTIME_USED,
                f"observed {rec['calls']} runtime call(s) in production "
                "tracing — definitely used", rule="runtime-tracking"))
        elif rec["registered_ts"]:
            days = max(0.0, (now - rec["registered_ts"]) / 86400)
            markers.append(Marker(
                symbol, MarkerKind.RUNTIME_ZERO,
                f"0 runtime calls observed over {days:.1f} day(s) of "
                "production tracing", rule="runtime-tracking"))
    return markers
