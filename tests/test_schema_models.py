"""Schema-model awareness: pydantic/Django/DRF fields are framework-used,
and function-signature annotations are real usage edges."""
import textwrap

import pytest

from codetruth import scan


@pytest.fixture(scope="module")
def schema_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("schema_repo")
    (repo / "models.py").write_text(textwrap.dedent("""\
        from enum import Enum

        from pydantic import BaseModel, field_validator, model_validator


        class Color(str, Enum):
            RED = "red"
            UNREFERENCED_TEAL = "teal"


        class DeadPalette(Enum):
            MAUVE = 1


        class Address(BaseModel):
            city: str
            postcode: str


        class User(BaseModel):
            name: str
            address: Address
            shade: Color

            class Config:
                frozen = True

            @field_validator("name")
            @classmethod
            def check_name(cls, v):
                return v

            @model_validator(mode="after")
            def check_all(self):
                return self


        class TimestampedUser(User):
            created_at: float


        class UnusedModel(BaseModel):
            orphan_field: int


        class PlainHelper:
            plain_attr = 1
    """), encoding="utf-8")
    (repo / "service.py").write_text(textwrap.dedent("""\
        from models import User


        def load_user(payload: dict) -> User:
            return User(**payload)
    """), encoding="utf-8")
    (repo / "annots.py").write_text(textwrap.dedent("""\
        class OnlyInSignature:
            def ping(self):
                return 1


        class OnlyInReturn:
            pass


        def process(x: OnlyInSignature) -> OnlyInReturn:
            return OnlyInReturn()
    """), encoding="utf-8")
    return repo


@pytest.fixture(scope="module")
def result(schema_repo):
    return scan(schema_repo, use_cache=False)


# ---- schema fields -----------------------------------------------------------

def test_model_fields_are_framework_used(result):
    for field in ("models:User.name", "models:User.address",
                  "models:Address.city"):
        rec = result.find(field)[0]
        assert rec.status.value == "definitely_used", field
        assert any("schema/model field" in e
                   for e in rec.evidence_against_deletion)


def test_subclass_of_model_is_schema_too(result):
    assert result.find("models:TimestampedUser.created_at")[0] \
        .status.value == "definitely_used"


def test_config_convention_recognised(result):
    assert result.find("models:User.Config")[0].status.value \
        == "definitely_used"
    assert result.find("models:User.Config.frozen")[0].status.value \
        == "definitely_used"


def test_plain_class_attr_not_marked(result):
    """Non-schema classes keep normal semantics — no blanket field marking."""
    rec = result.find("models:PlainHelper.plain_attr")[0]
    assert rec.status.value != "definitely_used"


def test_dead_model_still_flagged_at_class_level(result):
    """Field marking must not hide a genuinely-unreferenced model."""
    assert result.find("models:UnusedModel")[0].status.value == "likely_dead"


def test_field_annotation_keeps_nested_model_alive(result):
    """Address is referenced only by the `address: Address` field annotation."""
    assert result.find("models:Address")[0].status.value == "definitely_used"


# ---- signature annotation edges ------------------------------------------------

def test_bare_pydantic_validators_are_used(result):
    """Regression (found dogfooding on a real user repo): validators imported
    bare — `from pydantic import field_validator` — were missed by the
    dotted-only patterns and flagged safe_to_delete."""
    for sym in ("models:User.check_name", "models:User.check_all"):
        assert result.find(sym)[0].status.value == "definitely_used", sym


def test_enum_members_never_safe(result):
    """Regression: enum members are constructed BY VALUE (Color('red'),
    pydantic coercion) — the name never appears, so a member must never be
    safe_to_delete even with zero name references."""
    rec = result.find("models:Color.UNREFERENCED_TEAL")[0]
    assert rec.status.value == "uncertain_dynamic_risk"
    assert any("constructed by value" in e
               for e in rec.evidence_against_deletion)


def test_dead_enum_class_still_flagged(result):
    """The member caution must not hide a genuinely-unreferenced enum class."""
    assert result.find("models:DeadPalette")[0].status.value == "likely_dead"


def test_param_annotation_is_usage(result):
    assert result.find("annots:OnlyInSignature")[0].status.value \
        == "definitely_used"


def test_return_annotation_is_usage(result):
    rec = result.find("annots:OnlyInReturn")[0]
    assert rec.inbound_strong >= 1
    assert rec.status.value == "definitely_used"
