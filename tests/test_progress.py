"""Scan progress reporting and clean cancellation."""
import io

import pytest

from codetruth import scan
from codetruth.cli import main
from codetruth.core.progress import ProgressRenderer

from conftest import FIXTURES

PLAIN = FIXTURES / "plain_repo"


def test_progress_callback_receives_phases_and_counts():
    events = []
    scan(PLAIN, use_cache=False,
         progress=lambda phase, done, total, detail: events.append(
             (phase, done, total, detail)))

    extract = [e for e in events if e[0] == "extract"]
    assert extract, "per-file extract events expected"
    total = extract[0][2]
    assert total > 0
    assert [e[1] for e in extract] == list(range(1, total + 1))  # monotonic
    assert extract[-1][1] == total                               # completes
    assert all(e[3] for e in extract)                            # file names

    phases = [e[0] for e in events]
    for phase in ("edges", "rules", "classify", "verify"):
        assert phase in phases
    # graph phases come after extraction finishes
    assert phases.index("edges") > phases.index("extract")


def test_renderer_writes_and_clears_line():
    buf = io.StringIO()
    r = ProgressRenderer(stream=buf, min_interval=0.0)
    r("extract", 1, 10, "app/a.py")
    r("extract", 10, 10, "app/z.py")
    r("edges", 0, 0)
    out = buf.getvalue()
    assert "scanning 1/10 files — app/a.py" in out
    assert "scanning 10/10 files" in out
    assert "building graph…" in out
    r.close()
    assert buf.getvalue().endswith("\r")     # line erased for real output


def test_renderer_throttles_mid_run():
    buf = io.StringIO()
    r = ProgressRenderer(stream=buf, min_interval=3600.0)  # never mid-run
    r("extract", 2, 100, "first.py")         # first paint always renders
    buf.truncate(0), buf.seek(0)
    for i in range(3, 99):
        r("extract", i, 100, f"f{i}.py")
    assert buf.getvalue() == ""              # mid-run calls all throttled
    r("extract", 100, 100, "last.py")
    assert "100/100" in buf.getvalue()       # completion always renders


def test_no_progress_by_default_off_tty(capsys):
    # pytest's captured stderr is not a TTY, so auto mode stays silent.
    assert main(["scan", str(PLAIN), "--no-cache", "--limit", "1"]) == 0
    assert "scanning" not in capsys.readouterr().err


def test_keyboard_interrupt_exits_130(monkeypatch, capsys):
    import codetruth.cli as cli

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "scan", boom)
    assert main(["scan", str(PLAIN)]) == 130
    assert "cancelled" in capsys.readouterr().err
