from codetruth.core.models import SymbolType
from codetruth.languages.python.extractor import extract_repo

from conftest import FIXTURES


def _index(modules):
    return {s.id: s for m in modules for s in m.symbols}


def test_plain_repo_symbols():
    modules, warnings = extract_repo(FIXTURES / "plain_repo")
    assert not warnings
    syms = _index(modules)

    assert syms["app.used:used_func"].type is SymbolType.FUNCTION
    assert syms["app.used:_helper"].is_public is False
    assert syms["app.used"].type is SymbolType.MODULE
    assert syms["app.dynamic:Plugin"].type is SymbolType.CLASS
    assert syms["app.dynamic:Plugin.maybe_dead"].type is SymbolType.METHOD
    assert syms["app.config:DISPATCH"].type is SymbolType.VARIABLE

    # __all__ controls the exported flag on the re-exporting package.
    app_init = next(m for m in modules if m.name == "app")
    assert app_init.all_names == ["used_func"]

    # Test files are flagged.
    assert syms["tests.test_app:test_only_tested"].is_test


def test_decorators_recorded():
    modules, _ = extract_repo(FIXTURES / "fastapi_repo")
    syms = _index(modules)
    assert "app.get" in syms["main:read_items"].decorators
    assert "app.post" in syms["main:create_item"].decorators


def test_django_repo_extraction():
    modules, _ = extract_repo(FIXTURES / "django_repo")
    syms = _index(modules)
    assert "receiver" in syms["myapp.signals:on_save"].decorators
    assert syms["mysite.settings:SECRET_KEY"].type is SymbolType.VARIABLE
