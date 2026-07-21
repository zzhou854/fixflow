from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from app.application.auth import (
    AuthenticationError,
    AuthService,
    AuthUser,
)
from app.domain.enums import ActorType
from argon2 import PasswordHasher

SECRET = "unit-test-jwt-secret-that-is-longer-than-32-characters"


class FakeAuthRepository:
    def __init__(self, users: list[AuthUser]) -> None:
        self.users = users

    async def find_by_username(self, username: str) -> AuthUser | None:
        return next((user for user in self.users if user.username == username), None)

    async def find_by_id(self, user_id: UUID) -> AuthUser | None:
        return next((user for user in self.users if user.user_id == user_id), None)


@pytest.fixture
def auth_fixture() -> tuple[AuthService, AuthUser]:
    hasher = PasswordHasher()
    user = AuthUser(
        user_id=uuid4(),
        username="resident_demo",
        actor_type=ActorType.RESIDENT,
        is_active=True,
        password_hash=hasher.hash("correct-password"),
    )
    return AuthService(FakeAuthRepository([user]), jwt_secret=SECRET, password_hasher=hasher), user


@pytest.mark.asyncio
async def test_correct_login_returns_signed_identity(
    auth_fixture: tuple[AuthService, AuthUser],
) -> None:
    service, user = auth_fixture
    identity, token = await service.login(user.username, "correct-password")
    assert identity.user_id == user.user_id
    assert token.token != "correct-password"
    assert (await service.authenticate(token.token)) == identity


@pytest.mark.asyncio
@pytest.mark.parametrize("username,password", [("resident_demo", "wrong"), ("missing", "wrong")])
async def test_bad_login_uses_one_error(
    username: str, password: str, auth_fixture: tuple[AuthService, AuthUser]
) -> None:
    service, _ = auth_fixture
    with pytest.raises(AuthenticationError, match="INVALID_CREDENTIALS"):
        await service.login(username, password)


@pytest.mark.asyncio
async def test_inactive_user_cannot_login(auth_fixture: tuple[AuthService, AuthUser]) -> None:
    service, user = auth_fixture
    inactive = replace(user, is_active=False)
    service = AuthService(FakeAuthRepository([inactive]), jwt_secret=SECRET)
    with pytest.raises(AuthenticationError):
        await service.login(inactive.username, "correct-password")


def test_password_hash_is_not_plaintext(auth_fixture: tuple[AuthService, AuthUser]) -> None:
    _, user = auth_fixture
    assert user.password_hash != "correct-password"
    assert user.password_hash.startswith("$argon2")


@pytest.mark.asyncio
async def test_required_jwt_claims_are_present(
    auth_fixture: tuple[AuthService, AuthUser],
) -> None:
    service, user = auth_fixture
    _, token = await service.login(user.username, "correct-password")
    claims = jwt.decode(token.token, SECRET, algorithms=["HS256"])
    assert {"sub", "actor_type", "user_id", "iat", "exp", "token_id"} <= claims.keys()


@pytest.mark.asyncio
async def test_expired_token_is_rejected(auth_fixture: tuple[AuthService, AuthUser]) -> None:
    service, user = auth_fixture
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.user_id),
            "actor_type": "RESIDENT",
            "user_id": str(user.user_id),
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "token_id": str(uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="TOKEN_EXPIRED"):
        await service.authenticate(token)


@pytest.mark.asyncio
async def test_alg_none_token_is_rejected(auth_fixture: tuple[AuthService, AuthUser]) -> None:
    service, user = auth_fixture
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.user_id),
            "actor_type": "RESIDENT",
            "user_id": str(user.user_id),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "token_id": str(uuid4()),
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        await service.authenticate(token)


@pytest.mark.asyncio
async def test_missing_claim_is_rejected(auth_fixture: tuple[AuthService, AuthUser]) -> None:
    service, user = auth_fixture
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.user_id),
            "actor_type": "RESIDENT",
            "user_id": str(user.user_id),
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        await service.authenticate(token)


@pytest.mark.asyncio
async def test_token_role_must_match_current_database_user(
    auth_fixture: tuple[AuthService, AuthUser],
) -> None:
    service, user = auth_fixture
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.user_id),
            "actor_type": "OPERATOR",
            "user_id": str(user.user_id),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "token_id": str(uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        await service.authenticate(token)


@pytest.mark.asyncio
async def test_token_is_rejected_after_account_is_deactivated() -> None:
    hasher = PasswordHasher()
    user = AuthUser(
        user_id=uuid4(),
        username="resident_demo",
        actor_type=ActorType.RESIDENT,
        is_active=True,
        password_hash=hasher.hash("correct-password"),
    )
    repository = FakeAuthRepository([user])
    service = AuthService(repository, jwt_secret=SECRET, password_hasher=hasher)
    _, token = await service.login(user.username, "correct-password")
    repository.users[0] = replace(user, is_active=False)
    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        await service.authenticate(token.token)


@pytest.mark.asyncio
async def test_wrong_signature_is_rejected(auth_fixture: tuple[AuthService, AuthUser]) -> None:
    service, user = auth_fixture
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.user_id),
            "actor_type": "RESIDENT",
            "user_id": str(user.user_id),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "token_id": str(uuid4()),
        },
        "different-signing-secret-that-is-still-long-enough",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        await service.authenticate(token)
