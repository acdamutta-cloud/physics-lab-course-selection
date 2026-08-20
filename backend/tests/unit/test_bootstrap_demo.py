from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.bootstrap_demo import (
    _flush_in_dependency_order,
    demo_id,
    validate_demo_password,
)


def test_demo_ids_are_stable_and_entity_specific() -> None:
    assert demo_id("campus") == demo_id("campus")
    assert demo_id("campus") != demo_id("major")


def test_demo_password_validation() -> None:
    assert validate_demo_password("DemoPassword!2026") == "DemoPassword!2026"


@pytest.mark.parametrize("password", ["", "short", "x" * 129])
def test_demo_password_rejects_invalid_length(password: str) -> None:
    with pytest.raises(ValueError, match="密码"):
        validate_demo_password(password)


@pytest.mark.asyncio
async def test_dependency_groups_are_flushed_separately() -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    accounts = [object(), object()]
    profiles = [object(), object()]

    await _flush_in_dependency_order(session, accounts, profiles)

    assert session.add_all.call_args_list[0].args == (accounts,)
    assert session.add_all.call_args_list[1].args == (profiles,)
    assert session.flush.await_count == 2
