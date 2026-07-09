"""JavaScript/TypeScript plugin — end-to-end classification ground truth.

Proves the LanguagePlugin extension path: extraction + edges are JS-specific;
graph, evidence, ranking, clusters, and the textual backstop are the shared
core working unchanged on a second language.
"""
import pytest

pytest.importorskip("tree_sitter_language_pack")

from codetruth import scan  # noqa: E402

from conftest import FIXTURES  # noqa: E402

JS_REPO = FIXTURES / "js_repo"


@pytest.fixture(scope="module")
def js_scan():
    return scan(JS_REPO, language="javascript", use_cache=False)


def test_imported_and_called_are_used(js_scan):
    assert js_scan.find("src/api:fetchUser")[0].status.value == "definitely_used"


def test_helper_called_by_export_is_used(js_scan):
    assert js_scan.find("src/api:buildQuery")[0].status.value == "definitely_used"


def test_namespace_member_access_is_strong(js_scan):
    # index.js calls fmt.pretty(...) through `import * as fmt`.
    assert js_scan.find("src/format:pretty")[0].status.value == "definitely_used"


def test_module_private_orphan_is_safe(js_scan):
    rec = js_scan.find("src/api:orphanHelper")[0]
    assert rec.status.value == "safe_to_delete"
    assert rec.inbound_strong == 0 and rec.inbound_weak == 0


def test_unused_export_is_likely_dead(js_scan):
    # Exported = the package's public API; consumers are invisible.
    assert js_scan.find("src/api:unusedExport")[0].status.value == "likely_dead"


def test_string_wired_symbol_is_uncertain(js_scan):
    rec = js_scan.find("src/format:stringWired")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("stringWired" in e for e in rec.evidence_against_deletion)


def test_package_json_main_is_entrypoint(js_scan):
    rec = js_scan.find("src/index")[0]
    assert rec.status.value == "definitely_used"
    assert any("package.json" in e for e in rec.evidence_against_deletion)


def test_dead_clump_is_grouped(js_scan):
    a = js_scan.find("src/legacy:clumpA")[0]
    b = js_scan.find("src/legacy:clumpB")[0]
    for rec in (a, b):
        assert rec.status.value == "likely_dead"
        assert rec.cluster == ["src/legacy:clumpA", "src/legacy:clumpB"]


def test_external_base_method_never_safe(js_scan):
    """render() overrides a framework base — must carry the caution."""
    rec = js_scan.find("src/view:UserView.render")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("external base" in e for e in rec.evidence_against_deletion)


def test_this_method_call_is_strong(js_scan):
    # render() calls this.compute() — a strong member edge.
    rec = js_scan.find("src/view:UserView.compute")[0]
    assert rec.inbound_strong >= 1


def test_typescript_class_extracted(js_scan):
    assert js_scan.find("src/view:UserView")[0].status.value == "likely_dead"


def test_safe_never_has_usage_paths(js_scan):
    for rec in js_scan.records:
        if rec.status.value == "safe_to_delete":
            assert rec.inbound_strong == 0
            assert rec.inbound_weak == 0
