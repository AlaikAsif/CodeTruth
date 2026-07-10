"""JS beta-exit: tsconfig-path/monorepo alias resolution and Vue SFC parsing."""
import pytest

pytest.importorskip("tree_sitter_language_pack")

from codetruth import scan  # noqa: E402
from codetruth.languages.javascript.resolve import Resolver  # noqa: E402

from conftest import FIXTURES  # noqa: E402

TSCONFIG = FIXTURES / "js_tsconfig"
VUE = FIXTURES / "js_vue"


# ---- tsconfig path aliases --------------------------------------------------

@pytest.fixture(scope="module")
def alias_scan():
    return scan(TSCONFIG, language="javascript", use_cache=False)


def test_alias_import_resolves_symbol_used(alias_scan):
    """fetchUser is reached only via `import { fetchUser } from '@/api'`.
    Without tsconfig-path resolution it would look unreferenced."""
    assert alias_scan.find("src/api:fetchUser")[0].status.value \
        == "definitely_used"


def test_baseurl_nested_alias_resolves(alias_scan):
    # formatName via `~lib/*` -> src/lib/*
    assert alias_scan.find("src/lib/format:formatName")[0].status.value \
        == "definitely_used"


def test_unaliased_export_still_flagged(alias_scan):
    assert alias_scan.find("src/api:unusedApi")[0].status.value \
        == "likely_dead"


def test_resolver_unit():
    r = Resolver(TSCONFIG, {"src/api", "src/lib/format", "src/app"})
    assert r.resolve("@/api") == "src/api"
    assert r.resolve("~lib/format") == "src/lib/format"
    assert r.resolve("react") is None       # external, unresolved


def test_jsonc_tsconfig_parses():
    # tsconfig has // comments and trailing commas — must still load.
    r = Resolver(TSCONFIG, {"src/api"})
    assert r.paths  # parsed despite JSONC syntax


# ---- Vue SFC ----------------------------------------------------------------

@pytest.fixture(scope="module")
def vue_scan():
    return scan(VUE, language="javascript", use_cache=False)


def test_vue_script_import_is_used(vue_scan):
    """formatLabel is imported and called inside a .vue <script> block."""
    assert vue_scan.find("src/helpers:formatLabel")[0].status.value \
        == "definitely_used"


def test_vue_local_function_used_in_script(vue_scan):
    assert vue_scan.find("src/Button:handleClick")[0].status.value \
        == "definitely_used"


def test_vue_dead_script_function_is_flagged(vue_scan):
    rec = vue_scan.find("src/Button:neverUsedInScript")[0]
    assert rec.status.value == "safe_to_delete"


def test_vue_unused_export_flagged(vue_scan):
    assert vue_scan.find("src/helpers:unusedHelper")[0].status.value \
        == "likely_dead"


def test_vue_component_imported_via_dotvue(vue_scan):
    # main.js imports './Button.vue' — the .vue module must resolve.
    assert vue_scan.find("src/Button")[0].status.value == "definitely_used"
