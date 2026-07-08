from pathlib import Path

import pytest

from codetruth import scan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def plain_scan():
    return scan(FIXTURES / "plain_repo")


@pytest.fixture(scope="session")
def plain_scan_app_mode():
    return scan(FIXTURES / "plain_repo", treat_public_as_api=False)


@pytest.fixture(scope="session")
def fastapi_scan():
    return scan(FIXTURES / "fastapi_repo")


@pytest.fixture(scope="session")
def django_scan():
    return scan(FIXTURES / "django_repo")


def status_of(result, symbol_id: str) -> str:
    matches = result.find(symbol_id)
    assert matches, f"symbol not found: {symbol_id}"
    assert len(matches) == 1, f"ambiguous: {symbol_id} -> {[m.symbol for m in matches]}"
    return matches[0].status.value
