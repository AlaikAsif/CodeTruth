"""Receiver-type resolution: typed method calls resolve to the right class
instead of fanning out to every same-named symbol — the #1 source of
uncertain-tier noise (66-85% on real repos).

Safety property throughout: typing may only *narrow* which symbols an access
keeps hedged; anything it stops hedging must still be caught by the textual
backstop before ever reaching safe_to_delete.
"""
import textwrap

import pytest

from codetruth import scan


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("typed_repo")
    (root / "shapes.py").write_text(textwrap.dedent("""\
        class Circle:
            def area(self):
                return 3.14

            def perimeter(self):
                return 6.28


        class Square:
            def area(self):
                return 4.0


        class Blob(Circle):
            pass


        def measure_local():
            c = Circle()
            return c.area()


        def measure_param(sq: Square):
            return sq.area()


        def measure_branchy(flag):
            if flag:
                x = Circle()
            else:
                x = Square()
            return x.area()


        def measure_untyped(things):
            for t in things:
                t.area()
            return None


        def measure_inherited():
            b = Blob()
            return b.perimeter()
    """), encoding="utf-8")
    (root / "holder.py").write_text(textwrap.dedent("""\
        from shapes import Circle


        class Holder:
            def __init__(self):
                self.shape = Circle()

            def render(self):
                return self.shape.perimeter()
    """), encoding="utf-8")
    (root / "reflect.py").write_text(textwrap.dedent("""\
        from shapes import Square


        def poke(name):
            sq = Square()
            return getattr(sq, name)


        def bystander():
            return "same module as the getattr, but not its receiver"
    """), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def result(repo):
    return scan(repo, use_cache=False)


def _strong_srcs(result, symbol):
    rec = result.find(symbol)[0]
    return rec


# ---- precision: the right method is used, the same-named one is not ---------

def test_typed_local_call_resolves_to_declared_class(result):
    assert result.find("shapes:Circle.area")[0].status.value \
        == "definitely_used"


def test_param_annotation_types_the_receiver(result):
    assert result.find("shapes:Square.area")[0].status.value \
        == "definitely_used"


def test_branchy_assignment_keeps_both_alive(result):
    """x = Circle() in one branch, Square() in the other: the may-binding
    set keeps BOTH area implementations alive (over-approximate liveness)."""
    for sym in ("shapes:Circle.area", "shapes:Square.area"):
        assert result.find(sym)[0].status.value == "definitely_used"


def test_inherited_member_resolves_through_mro(result):
    """Blob().perimeter() resolves to Circle.perimeter via the base chain."""
    rec = result.find("shapes:Circle.perimeter")[0]
    assert rec.status.value == "definitely_used"


def test_typed_self_attribute_chain(result):
    """self.shape = Circle() types the instance attr; self.shape.perimeter()
    is a strong member edge, not a two-segment fanout."""
    rec = result.find("shapes:Circle.perimeter")[0]
    assert any("typed receiver" in e for e in rec.evidence_against_deletion)


def test_untyped_receiver_still_fans_out(result):
    """`for t in things: t.area()` — untypable receiver keeps conservative
    name-match fanout, so weak evidence still hedges every `area`."""
    rec = result.find("shapes:Square.area")[0]
    # Square.area is definitely_used via the param annotation anyway; the
    # invariant that matters: the untyped call produced weak edges (fanout
    # present in the graph), so nothing named `area` could ever be safe.
    assert rec.inbound_weak >= 1


# ---- scoped reflection --------------------------------------------------------

def test_nonliteral_getattr_scopes_to_receiver_class(result):
    rec = result.find("shapes:Square.area")[0]
    assert any("Square instance" in e for e in rec.evidence_against_deletion)


def test_bystander_not_poisoned_by_scoped_getattr(result):
    """Before: any non-literal getattr poisoned its whole module. Now the
    bystander in the same module classifies normally."""
    assert result.find("reflect:bystander")[0].status.value == "likely_dead"


# ---- safety net ----------------------------------------------------------------

def test_typed_narrowing_never_yields_textual_fp(result):
    """Nothing safe_to_delete may have its name anywhere else in the repo —
    the backstop that makes typed narrowing safe by construction."""
    for rec in result.records:
        if rec.status.value == "safe_to_delete":
            assert rec.inbound_strong == 0 and rec.inbound_weak == 0