"""JS framework coverage: Express/emitter callback entrypoints, package.json
scripts, and React JSX component usage (incl. local-only components)."""
import pytest

pytest.importorskip("tree_sitter_language_pack")

from codetruth import scan  # noqa: E402

from conftest import FIXTURES  # noqa: E402


# ---- Express / callbacks ----------------------------------------------------

@pytest.fixture(scope="module")
def express_scan():
    return scan(FIXTURES / "js_express", language="javascript", use_cache=False)


def test_route_handlers_are_used(express_scan):
    for sym in ("src/handlers:listUsers", "src/handlers:createUser"):
        rec = express_scan.find(sym)[0]
        assert rec.status.value == "definitely_used"
        assert any(".get(" in e or ".post(" in e
                   for e in rec.evidence_against_deletion)


def test_listen_callback_is_used(express_scan):
    rec = express_scan.find("src/server:onReady")[0]
    assert rec.status.value == "definitely_used"
    assert any("callback" in e for e in rec.evidence_against_deletion)


def test_unregistered_handler_is_flagged(express_scan):
    assert express_scan.find("src/handlers:neverRegistered")[0] \
        .status.value == "likely_dead"


def test_package_json_script_target_is_entrypoint(express_scan):
    rec = express_scan.find("src/server")[0]
    assert rec.status.value == "definitely_used"
    assert any("scripts" in e for e in rec.evidence_against_deletion)


def test_route_handlers_survive_strict_mode(express_scan):
    strict = scan(FIXTURES / "js_express", language="javascript",
                  use_cache=False, reachability="strict")
    # Handlers are real entry points even when nothing internal reaches them.
    assert strict.find("src/handlers:listUsers")[0].status.value \
        == "definitely_used"


# ---- React / JSX ------------------------------------------------------------

@pytest.fixture(scope="module")
def react_scan():
    return scan(FIXTURES / "js_react", language="javascript", use_cache=False)


def test_imported_component_used_via_jsx(react_scan):
    assert react_scan.find("src/UserCard:UserCard")[0].status.value \
        == "definitely_used"


def test_local_component_used_only_via_jsx_is_not_dead(react_scan):
    """The critical case: <LocalBadge/> is the only reference. If JSX element
    names didn't resolve, this would be a FALSE POSITIVE (safe_to_delete)."""
    rec = react_scan.find("src/App:LocalBadge")[0]
    assert rec.status.value == "definitely_used"
    assert rec.inbound_strong >= 1


def test_jsx_event_handler_reference_is_used(react_scan):
    # handleClick appears only as onClick={handleClick} in JSX.
    assert react_scan.find("src/App:handleClick")[0].status.value \
        == "definitely_used"


def test_truly_unused_local_is_safe(react_scan):
    rec = react_scan.find("src/App:UnusedWidget")[0]
    assert rec.status.value == "safe_to_delete"
    assert rec.inbound_strong == 0 and rec.inbound_weak == 0


def test_unrendered_export_is_flagged(react_scan):
    # Sidebar is exported but imported/rendered nowhere in the repo.
    assert react_scan.find("src/Sidebar:Sidebar")[0].status.value \
        == "likely_dead"
