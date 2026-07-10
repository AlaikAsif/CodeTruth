"""Cross-repo (workspace) scanning: HTTP route↔client matching and shared
imports overlay cross-service usage that single-repo analysis can't see."""
from codetruth import scan, scan_repos
from codetruth.core.crossrepo import _norm_path, paths_match

from conftest import FIXTURES

WS = FIXTURES / "workspace"
API = WS / "service_api"
CLIENT = WS / "service_client"


# ---- path matching primitives ----------------------------------------------

def test_norm_path_collapses_params_and_host():
    assert _norm_path("https://api.internal/users/42") == "/users/*"
    assert _norm_path("/users/{user_id}") == "/users/*"
    assert _norm_path("/orders?limit=5") == "/orders"


def test_paths_match_with_basepath_prefix():
    assert paths_match("/users/*", "/users/42")
    assert paths_match("/orders", "/api/v1/orders")   # client base-path prefix
    assert not paths_match("/orders", "/users/1")


# ---- shared-import overlay (the headline) -----------------------------------

def test_shared_symbol_dead_alone_but_kept_in_workspace():
    """serialize_user looks dead scanned alone, but service_client imports it."""
    alone = scan(API, use_cache=False)
    assert alone.find("shared_models:serialize_user")[0].status.value \
        == "likely_dead"

    ws = scan_repos([API, CLIENT], use_cache=False)
    rec = ws.repos["service_api"].find("shared_models:serialize_user")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("shared across repos" in e
               for e in rec.evidence_against_deletion)


def test_truly_unused_shared_symbol_stays_dead():
    ws = scan_repos([API, CLIENT], use_cache=False)
    rec = ws.repos["service_api"].find("shared_models:unused_local")[0]
    assert rec.status.value == "likely_dead"
    assert not any("cross-repo" in e.lower()
                   for e in rec.evidence_against_deletion)


# ---- HTTP route ↔ client matching -------------------------------------------

def test_consumed_route_is_linked_across_repos():
    ws = scan_repos([API, CLIENT], use_cache=False)
    reasons = " ".join(c.reason for c in ws.crossrefs)
    # httpx.get(".../users/42") in the client matches GET /users/{user_id}.
    assert "get_user" in " ".join(c.symbol for c in ws.crossrefs)
    assert "called across services" in reasons


def test_unconsumed_route_has_no_client_crossref():
    """The /internal/metrics endpoint is exposed but no client calls it —
    a candidate dead endpoint, correctly NOT linked to any consumer."""
    ws = scan_repos([API, CLIENT], use_cache=False)
    metrics_xrefs = [c for c in ws.crossrefs
                     if c.symbol.endswith(":metrics")
                     and "called across services" in c.reason]
    assert metrics_xrefs == []


def test_overlay_never_downgrades_used():
    """Route handlers are definitely_used in their own repo; the overlay must
    not touch that verdict."""
    ws = scan_repos([API, CLIENT], use_cache=False)
    assert ws.repos["service_api"].find("app:get_user")[0].status.value \
        == "definitely_used"


def test_workspace_summary_and_json():
    ws = scan_repos([API, CLIENT], use_cache=False)
    s = ws.summary()
    assert set(s["repos"]) == {"service_api", "service_client"}
    assert s["cross_references"] >= 2
    d = ws.to_dict()
    assert "cross_references" in d and "repos" in d
