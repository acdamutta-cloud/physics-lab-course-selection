import pytest

from scripts.create_admin import validate_credentials


def test_validate_credentials_normalizes_login_name() -> None:
    login_name, password = validate_credentials(
        "  admin  ", "SafePassword!2026"
    )

    assert login_name == "admin"
    assert password == "SafePassword!2026"


@pytest.mark.parametrize(
    ("login_name", "password", "message"),
    [
        ("", "SafePassword!2026", "登录名不能为空"),
        ("admin user", "SafePassword!2026", "不能包含空白字符"),
        ("admin", "too-short", "至少需要 12 个字符"),
        ("admin", "admin", "至少需要 12 个字符"),
    ],
)
def test_validate_credentials_rejects_unsafe_values(
    login_name: str, password: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_credentials(login_name, password)
