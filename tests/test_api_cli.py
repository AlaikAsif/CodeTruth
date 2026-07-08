from codetruth import check_deletion_safety
from codetruth.cli import main

from conftest import FIXTURES

PLAIN = FIXTURES / "plain_repo"


def test_check_exact_symbol(plain_scan):
    resp = check_deletion_safety(PLAIN, "app.used:_dead_private",
                                 result=plain_scan)
    assert resp["found"] and not resp["ambiguous"]
    assert resp["record"]["status"] == "safe_to_delete"
    assert resp["record"]["recommended_action"] == "delete"


def test_check_dotted_form(plain_scan):
    resp = check_deletion_safety(PLAIN, "app.used.dead_public",
                                 result=plain_scan)
    assert resp["found"] and not resp["ambiguous"]
    assert resp["record"]["status"] == "likely_dead"
    assert resp["record"]["recommended_action"] == "review_required"


def test_check_missing_symbol_warns_against_deletion(plain_scan):
    resp = check_deletion_safety(PLAIN, "no.such:symbol", result=plain_scan)
    assert resp["found"] is False
    assert "Do NOT delete" in resp["message"]


def test_cli_scan_runs(capsys):
    assert main(["scan", str(PLAIN), "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "safe_to_delete" in out


def test_cli_check_runs(capsys):
    assert main(["check", str(PLAIN), "app.used:_dead_private"]) == 0
    out = capsys.readouterr().out
    assert "safe_to_delete" in out


def test_cli_scan_json_export(tmp_path, capsys):
    out_file = tmp_path / "evidence.json"
    assert main(["scan", str(PLAIN), "--json", str(out_file)]) == 0
    assert out_file.is_file()


def test_mcp_server_importable():
    import codetruth.mcp_server as srv
    assert srv.mcp.name == "codetruth"
