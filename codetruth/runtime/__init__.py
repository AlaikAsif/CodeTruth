"""Phase 6 — runtime usage evidence.

`@codetruth.track` records real invocations of a function in a running app.
"Zero calls observed over N days" is the strongest evidence for deletion —
and the only answer to cross-service usage that static analysis can't see.

Production-hardened:
- **Multiprocess-safe**: each process appends to its own file
  (`runtime-<pid>.jsonl` next to the configured path); readers merge every
  sibling, so concurrent workers never contend or corrupt a shared log.
- **Interval flushing**: a daemon thread flushes counts every
  $CODETRUTH_FLUSH_INTERVAL seconds (default 60) — long-running servers
  don't have to exit cleanly for evidence to land.
- **Auto-instrumentation**: `instrument_package("pkg")` wraps every function
  and method of a package (already-imported modules immediately, future
  imports via an import hook) — no source edits needed.

Log location: $CODETRUTH_RUNTIME_LOG (default .codetruth/runtime.jsonl in
the CWD); the per-process suffix is added automatically.
Entries:
  {"event": "register", "symbol": "pkg.mod:func", "ts": 1710000000.0}
  {"event": "calls", "symbol": "pkg.mod:func", "count": 17, "ts": ...}
"""
from __future__ import annotations

import atexit
import functools
import importlib.util
import json
import os
import sys
import threading
import time
import types
from collections import Counter
from pathlib import Path

from ..core.models import Marker, MarkerKind

_lock = threading.Lock()
_counts: Counter = Counter()
_registered: set[str] = set()
_flusher_installed = False


def _base_log_path() -> Path:
    return Path(os.environ.get("CODETRUTH_RUNTIME_LOG",
                               ".codetruth/runtime.jsonl"))


def _log_path() -> Path:
    """Per-process log file: '<stem>-<pid><suffix>' beside the base path."""
    base = _base_log_path()
    return base.with_name(f"{base.stem}-{os.getpid()}{base.suffix}")


def _append(entry: dict) -> None:
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # tracing must never take the application down


def _flush() -> None:
    with _lock:
        counts = dict(_counts)
        _counts.clear()
    if not counts:
        return
    ts = time.time()
    for symbol, count in counts.items():
        _append({"event": "calls", "symbol": symbol, "count": count, "ts": ts})


def _flush_loop(interval: float) -> None:
    while True:
        time.sleep(interval)
        _flush()


def _install_flusher() -> None:
    global _flusher_installed
    if _flusher_installed:
        return
    _flusher_installed = True
    atexit.register(_flush)
    interval = float(os.environ.get("CODETRUTH_FLUSH_INTERVAL", "60"))
    if interval > 0:
        threading.Thread(target=_flush_loop, args=(interval,),
                         daemon=True, name="codetruth-flush").start()


def track(fn=None, *, name: str | None = None):
    """Decorator: register the symbol at import, count real invocations.

    Usage:  @codetruth.track          or   @codetruth.track(name="pkg.mod:func")
    """
    def decorate(func):
        if getattr(func, "_codetruth_tracked", False):
            return func
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

        wrapper._codetruth_tracked = True
        return wrapper

    return decorate(fn) if fn is not None else decorate


# --- auto-instrumentation ----------------------------------------------------

def _instrument_module(mod: types.ModuleType) -> int:
    """Wrap every function and public method defined in this module."""
    wrapped = 0
    for attr, obj in list(vars(mod).items()):
        if isinstance(obj, types.FunctionType) \
                and obj.__module__ == mod.__name__ \
                and not getattr(obj, "_codetruth_tracked", False):
            setattr(mod, attr, track(obj))
            wrapped += 1
        elif isinstance(obj, type) and obj.__module__ == mod.__name__:
            for mname, meth in list(vars(obj).items()):
                if isinstance(meth, types.FunctionType) \
                        and not mname.startswith("__") \
                        and not getattr(meth, "_codetruth_tracked", False):
                    setattr(obj, mname, track(meth))
                    wrapped += 1
    return wrapped


class _AutoTrackLoader:
    def __init__(self, loader):
        self._loader = loader

    def __getattr__(self, item):
        return getattr(self._loader, item)

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self._loader.exec_module(module)
        _instrument_module(module)


class _AutoTrackFinder:
    """Meta-path finder that instruments configured packages on import."""

    def __init__(self):
        self.packages: set[str] = set()
        self._resolving = threading.local()

    def _matches(self, fullname: str) -> bool:
        return any(fullname == p or fullname.startswith(p + ".")
                   for p in self.packages)

    def find_spec(self, fullname, path=None, target=None):
        if getattr(self._resolving, "active", False) or not self._matches(fullname):
            return None
        self._resolving.active = True
        try:
            spec = importlib.util.find_spec(fullname)
        except (ImportError, ValueError):
            return None
        finally:
            self._resolving.active = False
        if spec and spec.loader:
            spec.loader = _AutoTrackLoader(spec.loader)
        return spec


_FINDER = _AutoTrackFinder()


def instrument_package(package_name: str) -> int:
    """Auto-track every function/method of a package without source edits.

    Instruments modules already imported and installs an import hook for the
    rest. Returns the number of callables wrapped so far.
    """
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)
    _FINDER.packages.add(package_name)
    wrapped = 0
    for mod_name, mod in list(sys.modules.items()):
        if mod is not None and (mod_name == package_name
                                or mod_name.startswith(package_name + ".")):
            wrapped += _instrument_module(mod)
    _install_flusher()
    return wrapped


def autotrack() -> int:
    """Instrument every package named in $CODETRUTH_AUTOTRACK (comma-sep)."""
    wrapped = 0
    for pkg in os.environ.get("CODETRUTH_AUTOTRACK", "").split(","):
        if pkg.strip():
            wrapped += instrument_package(pkg.strip())
    return wrapped


# --- reading logs back -------------------------------------------------------

def _log_files(path: str | Path) -> list[Path]:
    """The given file plus every per-process sibling ('<stem>-*<suffix>')."""
    p = Path(path)
    files = [p] if p.is_file() else []
    if p.parent.is_dir():
        files += sorted(f for f in p.parent.glob(f"{p.stem}-*{p.suffix}")
                        if f.is_file())
    return files


def load_runtime_log(path: str | Path) -> dict[str, dict]:
    """Aggregate (merging per-process files): symbol -> {calls,
    registered_ts, last_ts}."""
    out: dict[str, dict] = {}
    for file in _log_files(path):
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
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
    """Convert runtime logs into evidence markers for the classifier."""
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
