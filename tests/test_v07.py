"""v0.7 adoption-loop features: SARIF, related_tests, report-fp."""
import json

from codetruth.cli import main
from codetruth.core.sarif import to_sarif

from conftest import FIXTURES

PLAIN = FIXTURES / "plain_repo"


# ---- SARIF -------------------------------------------------------------------

def test_sarif_structure_and_levels(plain_scan):
    doc = to_sarif(plain_scan)
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "CodeTruth"
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert "codetruth/safe-to-delete" in rule_ids

    results = run["results"]
    assert results, "candidates expected"
    by_rule = {}
    for r in results:
        by_rule.setdefault(r["ruleId"], []).append(r)
    # the gate tier is a warning; review tiers are notes
    assert all(r["level"] == "warning"
               for r in by_rule.get("codetruth/safe-to-delete", []))
    assert all(r["level"] == "note"
               for r in by_rule.get("codetruth/likely-dead", []))
    # every result is locatable and fingerprinted on the symbol id
    for r in results:
        loc = r["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"]
        assert loc["region"]["startLine"] >= 1
        assert r["partialFingerprints"]["codetruthSymbol/v1"]


def test_sarif_excludes_used_symbols(plain_scan):
    doc = to_sarif(plain_scan)
    texts = " ".join(r["message"]["text"] for r in doc["runs"][0]["results"])
    assert "app.used:used_func" not in texts


def test_cli_sarif_flag(tmp_path, capsys):
    out = tmp_path / "ct.sarif"
    assert main(["scan", str(PLAIN), "--sarif", str(out), "--limit", "0",
                 "--no-cache"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"


def test_sarif_written_even_when_ci_gate_fails(tmp_path, capsys):
    out = tmp_path / "gate.sarif"
    code = main(["scan", str(PLAIN), "--ci", "--sarif", str(out),
                 "--limit", "0", "--no-cache"])
    assert code == 1          # plain_repo has safe_to_delete fixtures
    assert out.is_file()      # ...but the SARIF still landed


# ---- related_tests on deletion plans ------------------------------------------

def test_deletion_plan_lists_related_tests(tmp_path):
    from codetruth import scan
    (tmp_path / "app.py").write_text(
        "def _doomed():\n    return 1\n\n\ndef used():\n    return 2\n",
        encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import used\n\n\ndef test_used():\n    assert used() == 2\n",
        encoding="utf-8")
    result = scan(tmp_path, use_cache=False)
    rec = result.find("app:_doomed")[0]
    assert rec.status.value == "safe_to_delete"
    # _doomed itself has no test references, but its module does — the plan
    # tells the human which test file exercises the surrounding code.
    assert rec.deletion_plan["related_tests"] == ["test_app.py"]


# ---- report-fp -----------------------------------------------------------------

def test_report_fp_generates_issue_body(capsys):
    assert main(["report-fp", str(PLAIN), "app.used:dead_public"]) == 0
    out = capsys.readouterr().out
    assert "Disputed verdict" in out
    assert "app.used:dead_public" in out
    assert "issues/new" in out
    assert "false-positive" in out


def test_report_fp_unknown_symbol(capsys):
    assert main(["report-fp", str(PLAIN), "no.such:thing"]) == 1
