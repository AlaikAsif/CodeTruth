"""End-to-end classification tests — the ground-truth table per fixture repo.

False positives (something used classified safe_to_delete) are the failures
that matter; every assertion here guards that boundary.
"""
from conftest import status_of


# ---- plain repo -------------------------------------------------------------

def test_called_and_imported_symbols_are_used(plain_scan):
    assert status_of(plain_scan, "app.used:used_func") == "definitely_used"
    assert status_of(plain_scan, "app.used:_helper") == "definitely_used"


def test_private_unreferenced_is_safe(plain_scan):
    assert status_of(plain_scan, "app.used:_dead_private") == "safe_to_delete"


def test_public_unreferenced_is_only_likely_dead_by_default(plain_scan):
    assert status_of(plain_scan, "app.used:dead_public") == "likely_dead"


def test_public_unreferenced_is_safe_in_app_mode(plain_scan_app_mode):
    assert status_of(plain_scan_app_mode, "app.used:dead_public") == "safe_to_delete"


def test_only_tested_symbol_is_likely_dead_with_evidence(plain_scan):
    rec = plain_scan.find("app.used:only_tested")[0]
    assert rec.status.value == "likely_dead"
    assert any("test" in e.lower() for e in rec.evidence_against_deletion)


def test_string_referenced_symbol_is_uncertain(plain_scan):
    rec = plain_scan.find("app.used:string_referenced")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("string" in e.lower() for e in rec.evidence_against_deletion)


def test_reflection_module_symbols_never_safe(plain_scan):
    # Non-literal getattr in app/dynamic.py: nothing there is provably dead.
    assert status_of(plain_scan, "app.dynamic:Plugin.maybe_dead") \
        == "uncertain_dynamic_risk"
    assert status_of(plain_scan, "app.dynamic:load") == "uncertain_dynamic_risk"
    assert status_of(plain_scan, "app.dynamic:Plugin") == "definitely_used"


def test_modules_are_never_safe_to_delete(plain_scan):
    for rec in plain_scan.records:
        if rec.type.value == "module":
            assert rec.status.value != "safe_to_delete"


# ---- fastapi repo -----------------------------------------------------------

def test_route_handlers_are_used(fastapi_scan):
    assert status_of(fastapi_scan, "main:read_items") == "definitely_used"
    assert status_of(fastapi_scan, "main:create_item") == "definitely_used"


def test_helper_called_by_route_is_used(fastapi_scan):
    assert status_of(fastapi_scan, "main:build_response") == "definitely_used"


def test_unrouted_helper_is_flagged(fastapi_scan):
    assert status_of(fastapi_scan, "main:never_called_helper") == "likely_dead"


def test_app_object_is_used(fastapi_scan):
    assert status_of(fastapi_scan, "main:app") == "definitely_used"


# ---- django repo ------------------------------------------------------------

def test_view_wired_in_urls_is_used(django_scan):
    assert status_of(django_scan, "myapp.views:home") == "definitely_used"


def test_string_wired_view_is_uncertain(django_scan):
    assert status_of(django_scan, "myapp.views:legacy_view") \
        == "uncertain_dynamic_risk"


def test_dead_view_is_flagged(django_scan):
    assert status_of(django_scan, "myapp.views:totally_dead_view") == "likely_dead"


def test_settings_constants_are_used(django_scan):
    assert status_of(django_scan, "mysite.settings:SECRET_KEY") == "definitely_used"


def test_signal_receiver_is_used(django_scan):
    assert status_of(django_scan, "myapp.signals:on_save") == "definitely_used"


def test_urlpatterns_is_used(django_scan):
    assert status_of(django_scan, "mysite.urls:urlpatterns") == "definitely_used"


def test_override_of_external_base_never_safe(plain_scan):
    """A method overriding an external base class may be called by the
    parent's code — it must carry a caution even with zero inbound edges."""
    rec = plain_scan.find("app.wrapping:Wrapper._handle_long_word")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("external base" in e for e in rec.evidence_against_deletion)


def test_comment_mention_blocks_safe_verdict(plain_scan):
    """The verification backstop: a name visible only in a comment is still
    a possible usage path — never safe_to_delete."""
    rec = plain_scan.find("app.notes:_mentioned_in_comment")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("Text occurrence" in e for e in rec.evidence_against_deletion)


def test_safe_verdicts_carry_verification_evidence(plain_scan):
    rec = plain_scan.find("app.used:_dead_private")[0]
    assert rec.status.value == "safe_to_delete"
    assert any("occurs nowhere else" in e for e in rec.evidence_for_deletion)


# ---- global invariant -------------------------------------------------------

def test_safe_to_delete_never_has_inbound_edges(plain_scan, fastapi_scan,
                                                django_scan):
    """The logic inversion: safe_to_delete requires zero usage paths found."""
    for result in (plain_scan, fastapi_scan, django_scan):
        for rec in result.records:
            if rec.status.value == "safe_to_delete":
                assert rec.inbound_strong == 0
                assert rec.inbound_weak == 0
