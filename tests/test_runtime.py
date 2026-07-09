import json
import subprocess
import sys
import textwrap
import time

from codetruth import scan, track
from codetruth.core.models import MarkerKind
from codetruth.runtime import (_flush, instrument_package, load_runtime_log,
                               load_runtime_markers)

from conftest import FIXTURES


def test_track_decorator_logs_calls(tmp_path, monkeypatch):
    log = tmp_path / "runtime.jsonl"
    monkeypatch.setenv("CODETRUTH_RUNTIME_LOG", str(log))

    @track(name="fixture.mod:tracked_fn")
    def tracked_fn():
        return 7

    assert tracked_fn() == 7
    assert tracked_fn() == 7
    _flush()

    data = load_runtime_log(log)
    assert data["fixture.mod:tracked_fn"]["calls"] == 2
    assert data["fixture.mod:tracked_fn"]["registered_ts"] is not None


def test_runtime_markers(tmp_path):
    log = tmp_path / "runtime.jsonl"
    entries = [
        {"event": "register", "symbol": "a:used", "ts": 1000.0},
        {"event": "calls", "symbol": "a:used", "count": 5, "ts": 2000.0},
        {"event": "register", "symbol": "a:never", "ts": 1000.0},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    markers = load_runtime_markers(log, {"a:used", "a:never", "a:untracked"})
    kinds = {m.symbol: m.kind for m in markers}
    assert kinds["a:used"] is MarkerKind.RUNTIME_USED
    assert kinds["a:never"] is MarkerKind.RUNTIME_ZERO
    assert "a:untracked" not in kinds


def test_runtime_evidence_promotes_to_used(tmp_path):
    """Runtime observation overrides static 'looks dead'."""
    log = tmp_path / "runtime.jsonl"
    entries = [
        {"event": "register", "symbol": "app.used:dead_public", "ts": 1000.0},
        {"event": "calls", "symbol": "app.used:dead_public", "count": 3,
         "ts": 2000.0},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    result = scan(FIXTURES / "plain_repo", runtime_log=log)
    rec = result.find("app.used:dead_public")[0]
    assert rec.status.value == "definitely_used"
    assert any("runtime" in e.lower() for e in rec.evidence_against_deletion)


def test_multiprocess_logs_merge(tmp_path):
    """Two concurrent processes tracking the same symbol write separate
    per-pid files; the reader merges them into one count."""
    log = tmp_path / "runtime.jsonl"
    script = textwrap.dedent("""\
        from codetruth import track

        @track(name="mp.mod:worker")
        def worker():
            return 1

        for _ in range(3):
            worker()
    """)
    env = {"CODETRUTH_RUNTIME_LOG": str(log), "CODETRUTH_FLUSH_INTERVAL": "0"}
    import os
    procs = [subprocess.run([sys.executable, "-c", script],
                            env={**os.environ, **env}, capture_output=True,
                            text=True) for _ in range(2)]
    for p in procs:
        assert p.returncode == 0, p.stderr

    data = load_runtime_log(log)
    assert data["mp.mod:worker"]["calls"] == 6
    # Two distinct per-pid files were produced.
    assert len(list(tmp_path.glob("runtime-*.jsonl"))) == 2


def test_instrument_package_wraps_without_source_edits(tmp_path, monkeypatch):
    """Auto-instrumentation: already-imported modules are wrapped in place,
    and modules imported afterwards are wrapped by the import hook."""
    monkeypatch.setenv("CODETRUTH_RUNTIME_LOG", str(tmp_path / "runtime.jsonl"))
    pkg = tmp_path / "trackme"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "early.py").write_text(
        "def early_fn():\n    return 'early'\n", encoding="utf-8")
    (pkg / "late.py").write_text(
        "def late_fn():\n    return 'late'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    import importlib
    early = importlib.import_module("trackme.early")
    try:
        wrapped = instrument_package("trackme")
        assert wrapped >= 1
        assert early.early_fn() == "early"          # already-imported path
        late = importlib.import_module("trackme.late")
        assert late.late_fn() == "late"             # import-hook path
        _flush()

        data = load_runtime_log(tmp_path / "runtime.jsonl")
        assert data["trackme.early:early_fn"]["calls"] == 1
        assert data["trackme.late:late_fn"]["calls"] == 1
    finally:
        for mod in ("trackme", "trackme.early", "trackme.late"):
            sys.modules.pop(mod, None)
        from codetruth.runtime import _FINDER
        _FINDER.packages.discard("trackme")


def test_interval_flush_lands_without_exit(tmp_path):
    """A long-running process doesn't need to exit for counts to land."""
    log = tmp_path / "runtime.jsonl"
    script = textwrap.dedent("""\
        import time
        from codetruth import track

        @track(name="iv.mod:beat")
        def beat():
            return 1

        beat()
        time.sleep(1.2)   # > flush interval; process still alive
        import os
        os._exit(0)       # hard exit: atexit flush never runs
    """)
    import os
    env = {**os.environ, "CODETRUTH_RUNTIME_LOG": str(log),
           "CODETRUTH_FLUSH_INTERVAL": "0.3"}
    p = subprocess.run([sys.executable, "-c", script], env=env,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    data = load_runtime_log(log)
    assert data["iv.mod:beat"]["calls"] == 1


def test_runtime_zero_strengthens_deletion_evidence(tmp_path):
    log = tmp_path / "runtime.jsonl"
    entries = [
        {"event": "register", "symbol": "app.used:_dead_private", "ts": 1000.0},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    result = scan(FIXTURES / "plain_repo", runtime_log=log)
    rec = result.find("app.used:_dead_private")[0]
    assert rec.status.value == "safe_to_delete"
    assert any("0 runtime calls" in e for e in rec.evidence_for_deletion)
