from uuid import uuid4

import pytest

from app.cache import auth_principals
from app.schemas.auth import UserProfile


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def eval(self, _script, _numkeys, key, token):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


@pytest.mark.asyncio
async def test_encrypted_profile_cache_hit_avoids_database(monkeypatch):
    monkeypatch.setattr(auth_principals, "_secret", lambda: b"test-secret")
    redis = FakeRedis()
    user_id = uuid4()
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return UserProfile(
            id=user_id,
            login_name="20260001",
            user_type="STUDENT",
            name="Sensitive Name",
            student_no="20260001",
        )

    first = await auth_principals.get_or_build_profile(user_id, build, redis=redis)
    second = await auth_principals.get_or_build_profile(user_id, build, redis=redis)

    assert first == second
    assert calls == 1
    key = auth_principals.auth_profile_key(user_id)
    assert str(user_id) not in key
    assert b"Sensitive Name" not in redis.values[key]
    assert b"20260001" not in redis.values[key]


@pytest.mark.asyncio
async def test_bad_ciphertext_falls_back_without_exposing_it(monkeypatch):
    monkeypatch.setattr(auth_principals, "_secret", lambda: b"test-secret")
    redis = FakeRedis()
    user_id = uuid4()
    key = auth_principals.auth_profile_key(user_id)
    redis.values[key] = b"not-valid-ciphertext"

    async def build():
        return UserProfile(id=user_id, login_name="student", user_type="STUDENT")

    result = await auth_principals.get_or_build_profile(user_id, build, redis=redis)
    assert result.login_name == "student"
    assert redis.values[key] != b"not-valid-ciphertext"
