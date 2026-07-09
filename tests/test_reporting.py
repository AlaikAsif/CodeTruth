"""Reporting/DX: --min-rank, --group, --ci gate, and the HTML report."""
from codetruth.cli import main
from codetruth.core.report import render_html

from conftest import FIXTURES

PLAIN = FIXTURES / "plain_repo"


def test_min_rank_filters_low_ranked(capsys):
    main(["scan", str(PLAIN), "--min-rank", "0.9", "--limit", "200"])
    out = capsys.readouterr().out
    # Every printed candidate row shows its rank as the first number; with a
    # 0.90 floor only safe_to_delete (>=0.95) rows may appear.
    for line in out.splitlines():
        if line.startswith("[") and "]" in line:
            rank = float(line.split("]")[1].split()[0])
            assert rank >= 0.9


def test_ci_gate_fails_when_safe_to_delete_exists(capsys):
    # plain_repo (library mode) has at least one safe_to_delete fixture.
    code = main(["scan", str(PLAIN), "--ci"])
    assert code == 1
    err = capsys.readouterr().err
    assert "CI gate" in err


def test_ci_gate_passes_when_nothing_safe(capsys):
    # Filtering to a status with no safe candidates still evaluates the gate
    # on the real counts; use app-less library scan where a safe fixture
    # exists, but verify the gate is 0 when we point at a repo with none.
    code = main(["scan", str(FIXTURES / "fastapi_repo"), "--ci"])
    assert code == 0


def test_group_by_file(capsys):
    main(["scan", str(PLAIN), "--group", "--limit", "200"])
    out = capsys.readouterr().out
    # Grouped output prints file headers followed by indented rows.
    assert "app/" in out or "app\\" in out
    assert "\n  [" in out


def test_html_report_is_selfcontained(plain_scan, tmp_path):
    html = render_html(plain_scan)
    assert html.startswith("<!doctype html>")
    assert "CodeTruth report" in html
    # No external asset references (strict CSP-safe / offline).
    assert "http://" not in html and "https://" not in html
    assert "src=" not in html


def test_cli_html_flag_writes_file(tmp_path):
    out = tmp_path / "report.html"
    main(["scan", str(PLAIN), "--html", str(out), "--limit", "0"])
    assert out.is_file()
    assert "<table" in out.read_text(encoding="utf-8")
