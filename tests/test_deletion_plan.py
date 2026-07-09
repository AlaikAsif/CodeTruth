"""Advisory deletion_plan tests: the plan describes; the tool never applies."""
import json as jsonlib

from codetruth import plan_deletion
from codetruth.cli import main

from conftest import FIXTURES

PLAIN = FIXTURES / "plain_repo"


def test_safe_record_carries_plan(plain_scan):
    rec = plain_scan.find("app.plannable:_doomed")[0]
    assert rec.status.value == "safe_to_delete"
    plan = rec.deletion_plan
    assert plan is not None
    # Span starts at the decorator, not the def line.
    assert plan["span"]["start_line"] == 11
    assert plan["span"]["end_line"] == 13
    # functools is only used by the doomed symbol; json is used elsewhere.
    orphaned = {o["name"] for o in plan["orphaned_imports"]}
    assert orphaned == {"functools"}
    assert "never applies" in plan["note"]


def test_plan_for_reviewed_symbol_carries_warning(plain_scan):
    resp = plan_deletion(PLAIN, "app.used:dead_public", result=plain_scan)
    assert resp["found"]
    assert resp["status"] != "safe_to_delete"
    assert resp["warning"] is not None
    assert resp["plan"]["span"]["start_line"] == 13


def test_plan_lists_dunder_all_entry(plain_scan):
    resp = plan_deletion(PLAIN, "app.plannable:public_api", result=plain_scan)
    assert resp["plan"]["dunder_all_entry"]["line"] == 4


def test_plan_for_module_is_whole_file_note(plain_scan):
    resp = plan_deletion(PLAIN, "app.notes", result=plain_scan)
    assert resp["plan"]["kind"] == "module"


def test_cli_plan_command(capsys):
    assert main(["plan", str(PLAIN), "app.plannable:_doomed"]) == 0
    out = jsonlib.loads(capsys.readouterr().out)
    assert out["plan"]["orphaned_imports"][0]["name"] == "functools"
