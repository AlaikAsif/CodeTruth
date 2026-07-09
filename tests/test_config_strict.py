"""Tests for .codetruth.toml config, `# codetruth: keep`, and strict
reachability (useless-clump detection with cluster grouping)."""
import textwrap

import pytest

from codetruth import scan


@pytest.fixture()
def config_repo(tmp_path):
    (tmp_path / "svc").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / ".codetruth.toml").write_text(textwrap.dedent("""\
        [codetruth]
        app_mode = true
        entrypoints = ["svc.jobs:nightly"]
        ignore_paths = ["vendor/"]
    """), encoding="utf-8")
    (tmp_path / "svc" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "svc" / "jobs.py").write_text(textwrap.dedent("""\
        def nightly():
            return 1


        def unreferenced():
            return 2
    """), encoding="utf-8")
    (tmp_path / "svc" / "keepme.py").write_text(textwrap.dedent("""\
        def kept():  # codetruth: keep
            return 3
    """), encoding="utf-8")
    (tmp_path / "vendor" / "junk.py").write_text(
        "def vendored():\n    return 0\n", encoding="utf-8")
    return tmp_path


def test_declared_entrypoint_is_used(config_repo):
    result = scan(config_repo, use_cache=False)
    rec = result.find("svc.jobs:nightly")[0]
    assert rec.status.value == "definitely_used"
    assert any(".codetruth.toml" in e for e in rec.evidence_against_deletion)


def test_app_mode_from_config(config_repo):
    """app_mode=true in the toml means public symbols can be safe_to_delete
    without passing treat_public_as_api explicitly."""
    result = scan(config_repo, use_cache=False)
    assert result.find("svc.jobs:unreferenced")[0].status.value \
        == "safe_to_delete"


def test_keep_comment_marks_entrypoint(config_repo):
    result = scan(config_repo, use_cache=False)
    rec = result.find("svc.keepme:kept")[0]
    assert rec.status.value == "definitely_used"
    assert any("codetruth: keep" in e for e in rec.evidence_against_deletion)


def test_ignored_paths_not_scanned(config_repo):
    result = scan(config_repo, use_cache=False)
    assert not result.find("vendor.junk:vendored")


@pytest.fixture()
def clump_repo(tmp_path):
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        from fastapi import FastAPI

        app = FastAPI()


        @app.get("/x")
        def handler():
            return helper_a()


        def helper_a():
            return 1


        def clump_a():
            return clump_b()


        def clump_b():
            return clump_a
    """), encoding="utf-8")
    return tmp_path


def test_strict_mode_finds_useless_clump(clump_repo):
    """Two functions that call each other but are never reached from any
    entry point form an unreachable clump."""
    result = scan(clump_repo, use_cache=False, reachability="strict")
    a = result.find("app:clump_a")[0]
    b = result.find("app:clump_b")[0]
    for rec in (a, b):
        assert rec.status.value == "likely_dead"
        assert rec.cluster == ["app:clump_a", "app:clump_b"]
        assert any("Strict mode" in e for e in rec.evidence_for_deletion)
        assert any("cluster" in e for e in rec.evidence_for_deletion)


def test_strict_mode_keeps_entrypoint_chain_alive(clump_repo):
    result = scan(clump_repo, use_cache=False, reachability="strict")
    assert result.find("app:handler")[0].status.value == "definitely_used"
    assert result.find("app:helper_a")[0].status.value == "definitely_used"


def test_default_mode_is_untouched_by_clumps(clump_repo):
    """Library-mode semantics unchanged: public mutually-referencing symbols
    stay used unless strict mode is requested."""
    result = scan(clump_repo, use_cache=False)
    assert result.find("app:clump_a")[0].status.value == "definitely_used"
