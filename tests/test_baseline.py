"""Baseline/CI mode: accept existing findings, fail only on new dead code."""
import json
import textwrap

import pytest

from codetruth import scan
from codetruth.cli import main
from codetruth.core.baseline import (default_path, diff_against_baseline,
                                     load_baseline)


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / ".codetruth.toml").write_text(
        "[codetruth]\napp_mode = true\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        def main():
            return helper()


        def helper():
            return 1


        def old_dead():
            return "existing dead code, accepted at baseline time"


        if __name__ == "__main__":
            main()
    """), encoding="utf-8")
    return tmp_path


def _no_cache(repo):
    return ["--no-cache"] if repo else []


def test_ci_without_baseline_fails_on_existing(repo, capsys):
    assert main(["scan", str(repo), "--ci", "--no-cache"]) == 1
    assert "codetruth baseline" in capsys.readouterr().err


def test_baseline_then_ci_passes(repo, capsys):
    assert main(["baseline", str(repo), "--no-cache"]) == 0
    assert default_path(repo).is_file()
    assert main(["scan", str(repo), "--ci", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "no new dead code beyond the baseline" in out


def test_new_dead_code_fails_and_lists_only_new(repo, capsys):
    main(["baseline", str(repo), "--no-cache"])
    capsys.readouterr()
    src = (repo / "app.py").read_text(encoding="utf-8")
    (repo / "app.py").write_text(
        src + "\n\ndef fresh_dead():\n    return 'introduced in this PR'\n",
        encoding="utf-8")

    assert main(["scan", str(repo), "--ci", "--no-cache"]) == 1
    err = capsys.readouterr().err
    assert "app:fresh_dead" in err
    assert "old_dead" not in err          # accepted finding not re-flagged
    assert "NEW safe_to_delete" in err


def test_resolved_findings_reported(repo, capsys):
    main(["baseline", str(repo), "--no-cache"])
    capsys.readouterr()
    # clean up the accepted dead code
    src = (repo / "app.py").read_text(encoding="utf-8")
    (repo / "app.py").write_text(
        src.replace('def old_dead():\n    return "existing dead code, '
                    'accepted at baseline time"\n', ""), encoding="utf-8")
    assert main(["scan", str(repo), "--ci", "--no-cache"]) == 0
    assert "resolved" in capsys.readouterr().out


def test_baseline_file_does_not_poison_scan(repo):
    """The baseline is a JSON file full of symbol ids at the repo root — it
    must be invisible to string-reference scanning, or writing it would flip
    every accepted safe_to_delete to uncertain_dynamic_risk."""
    before = scan(repo, use_cache=False)
    assert before.find("app:old_dead")[0].status.value == "safe_to_delete"
    main(["baseline", str(repo), "--no-cache"])
    after = scan(repo, use_cache=False)
    assert after.find("app:old_dead")[0].status.value == "safe_to_delete"


def test_provability_transition_counts_as_new(repo):
    """A symbol accepted as likely_dead that becomes safe_to_delete is newly
    actionable — the gate must surface it."""
    main(["baseline", str(repo), "--no-cache"])
    path = default_path(repo)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["findings"]["app:old_dead"] = "likely_dead"   # simulate older state
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = scan(repo, use_cache=False)
    diff = diff_against_baseline(result, load_baseline(path))
    assert any(r.symbol == "app:old_dead" for r in diff.new_safe)


def test_custom_baseline_path(repo, tmp_path, capsys):
    alt = tmp_path / "alt-baseline.json"
    assert main(["baseline", str(repo), "--no-cache", "-o", str(alt)]) == 0
    assert alt.is_file()
    assert main(["scan", str(repo), "--ci", "--no-cache",
                 "--baseline", str(alt)]) == 0
