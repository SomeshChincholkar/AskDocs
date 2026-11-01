import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from app.config import Settings
from app.services.auth import (
    AuthError,
    AuthenticatedUser,
    create_access_token,
    get_user_from_token,
    hash_password,
    verify_password,
)
from tests.auth_support import FakePool, new_user


def test_argon2_password_hash_and_verification():
    async def run():
        password_hash = await hash_password("correct horse battery")
        assert password_hash.startswith("$argon2id$")
        assert await verify_password(password_hash, "correct horse battery")
        assert not await verify_password(password_hash, "wrong password")

    asyncio.run(run())


def test_jwt_validation_checks_signature_expiry_version_and_user_type():
    settings = Settings(jwt_secret="auth-token-test-secret-value-is-long-enough")
    pool = FakePool()
    anonymous_id = uuid4()
    new_user(pool, anonymous_id)
    anonymous = AuthenticatedUser(anonymous_id, None, True, 0)
    token, _ = create_access_token(anonymous, settings)

    assert asyncio.run(get_user_from_token(pool, settings, token)).id == anonymous_id

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(AuthError):
        asyncio.run(get_user_from_token(pool, settings, tampered))

    expired = jwt.encode(
        {
            "sub": str(anonymous_id),
            "typ": "anon",
            "ver": 0,
            "iat": datetime.now(timezone.utc) - timedelta(days=2),
            "exp": datetime.now(timezone.utc) - timedelta(days=1),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthError):
        asyncio.run(get_user_from_token(pool, settings, expired))

    wrong_version, _ = create_access_token(
        AuthenticatedUser(anonymous_id, None, True, 1),
        settings,
    )
    with pytest.raises(AuthError):
        asyncio.run(get_user_from_token(pool, settings, wrong_version))

    pool.users[anonymous_id].update(
        username="registered",
        is_anonymous=False,
        token_version=1,
    )
    old_anon_type, _ = create_access_token(
        AuthenticatedUser(anonymous_id, "registered", True, 1),
        settings,
    )
    with pytest.raises(AuthError):
        asyncio.run(get_user_from_token(pool, settings, old_anon_type))
    assert asyncio.run(
        get_user_from_token(pool, settings, token, strict=False)
    ) is None


def test_registered_user_token_is_valid():
    settings = Settings(jwt_secret="auth-token-test-secret-value-is-long-enough")
    pool = FakePool()
    user_id = uuid4()
    new_user(pool, user_id, "valid-user", "hash", anonymous=False, version=3)
    token, _ = create_access_token(
        AuthenticatedUser(user_id, "valid-user", False, 3),
        settings,
    )

    user = asyncio.run(get_user_from_token(pool, settings, token))

    assert user is not None
    assert user.id == user_id
    assert user.username == "valid-user"
    assert user.is_anonymous is False


def test_jwt_secret_is_required_and_must_be_long_enough(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValueError):
        Settings(_env_file=None)
    with pytest.raises(ValueError):
        Settings(jwt_secret="too-short", _env_file=None)
