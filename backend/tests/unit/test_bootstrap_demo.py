import pytest

from scripts.bootstrap_demo import demo_id, validate_demo_password


def test_demo_ids_are_stable_and_entity_specific() -> None:
    assert demo_id("campus") == demo_id("campus")
    assert demo_id("campus") != demo_id("major")


def test_demo_password_validation() -> None:
    assert validate_demo_password("DemoPassword!2026") == "DemoPassword!2026"


@pytest.mark.parametrize("password", ["", "short", "x" * 129])
def test_demo_password_rejects_invalid_length(password: str) -> None:
    with pytest.raises(ValueError, match="密码"):
        validate_demo_password(password)
