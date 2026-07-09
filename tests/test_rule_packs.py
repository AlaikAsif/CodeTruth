"""New Layer-3 rule packs + singledispatch coverage. Rules match decorator
names syntactically, so no framework needs to be installed."""
import textwrap

import pytest

from codetruth import scan


@pytest.fixture()
def framework_repo(tmp_path):
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        import functools
        from sqlalchemy import event
        import typer

        cli = typer.Typer()


        @cli.command()
        def serve():
            return 1


        @event.listens_for(object, "before_insert")
        def stamp(mapper, connection, target):
            return 2


        @functools.singledispatch
        def render(value):
            return str(value)


        @render.register
        def _(value: int):
            return "int"
    """), encoding="utf-8")
    return tmp_path


def test_typer_command_is_used(framework_repo):
    assert scan(framework_repo, use_cache=False).find("app:serve")[0] \
        .status.value == "definitely_used"


def test_sqlalchemy_listener_is_used(framework_repo):
    assert scan(framework_repo, use_cache=False).find("app:stamp")[0] \
        .status.value == "definitely_used"


def test_singledispatch_registration_is_used(framework_repo):
    """The @render.register function (named `_`) is invoked via dispatch."""
    result = scan(framework_repo, use_cache=False)
    rec = next(r for r in result.records
               if r.symbol.startswith("app:render.") or r.name == "_")
    assert rec.status.value == "definitely_used"


def test_dunder_main_entrypoint(tmp_path):
    pkg = tmp_path / "tool"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "def main():\n    return run()\n\n\ndef run():\n    return 1\n",
        encoding="utf-8")
    result = scan(tmp_path, use_cache=False)
    assert result.find("tool.__main__:main")[0].status.value == "definitely_used"
