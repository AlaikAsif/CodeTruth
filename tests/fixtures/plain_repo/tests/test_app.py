from app.used import only_tested


def test_only_tested():
    assert "test suite" in only_tested()
