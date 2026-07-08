import json

from codetruth import scan, track
from codetruth.core.models import MarkerKind
from codetruth.runtime import _flush, load_runtime_log, load_runtime_markers

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
