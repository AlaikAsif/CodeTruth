"""Schema-model awareness: pydantic/Django/DRF fields are framework-used,
and function-signature annotations are real usage edges."""
import textwrap

import pytest

from codetruth import scan


@pytest.fixture(scope="module")
def schema_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("schema_repo")
    (repo / "models.py").write_text(textwrap.dedent("""\
        from pydantic import BaseModel


        class Address(BaseModel):
            city: str
            postcode: str


        class User(BaseModel):
            name: str
            address: Address

            class Config:
                frozen = True


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

def test_param_annotation_is_usage(result):
    assert result.find("annots:OnlyInSignature")[0].status.value \
        == "definitely_used"


def test_return_annotation_is_usage(result):
    rec = result.find("annots:OnlyInReturn")[0]
    assert rec.inbound_strong >= 1
    assert rec.status.value == "definitely_used"
