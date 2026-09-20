"""Resident registration, authentication, and short-lived JWT access tokens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.domain.enums import ActorType


class AuthenticationError(Exception):
    """Stable authentication failure without account-existence disclosure."""

    def __init__(self, code: str = "INVALID_CREDENTIALS") -> None:
        super().__init__(code)
        self.code = code


class RegistrationError(Exception):
    """Stable resident-registration failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AuthUser:
    user_id: UUID
    username: str
    actor_type: ActorType
    is_active: bool
    password_hash: str


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    user_id: UUID
    username: str
    actor_type: ActorType

    @property
    def actor_id(self) -> UUID:
        return self.user_id


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_at: datetime


class AuthUserRepository(Protocol):
    async def find_by_username(self, username: str) -> AuthUser | None: ...

    async def find_by_id(self, user_id: UUID) -> AuthUser | None: ...

    async def register_resident(
        self,
        *,
        username: str,
        password_hash: str,
        community_name: str,
        building_no: str,
        unit_no: str,
        room_no: str,
    ) -> AuthUser: ...


class AuthService:
    """Register residents, authenticate users, and validate signed tokens."""

    def __init__(
        self,
        repository: AuthUserRepository,
        *,
        jwt_secret: str,
        jwt_algorithm: str = "HS256",
        access_token_minutes: int = 30,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        if jwt_algorithm != "HS256":
            raise ValueError("release 1 supports only HS256")
        if len(jwt_secret) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")
        if access_token_minutes <= 0:
            raise ValueError("access token lifetime must be positive")
        self._repository = repository
        self._secret = jwt_secret
        self._algorithm = jwt_algorithm
        self._minutes = access_token_minutes
        self._hasher = password_hasher or PasswordHasher()
        self._dummy_hash = self._hasher.hash("fixflow-dummy-password-never-used")

    async def login(
        self, username: str, password: str
    ) -> tuple[AuthenticatedIdentity, AccessToken]:
        user = await self._repository.find_by_username(username.strip())
        verified = self._verify(user.password_hash if user else self._dummy_hash, password)
        if user is None or not user.is_active or not verified:
            raise AuthenticationError()
        identity = self._identity(user)
        return identity, self._issue(identity)

    async def register_resident(
        self,
        username: str,
        password: str,
        community_name: str,
        building_no: str,
        unit_no: str,
        room_no: str,
    ) -> tuple[AuthenticatedIdentity, AccessToken]:
        normalized_username = username.strip()
        user = await self._repository.register_resident(
            username=normalized_username,
            password_hash=self.hash_password(password),
            community_name=community_name.strip(),
            building_no=self._property_part(building_no, "栋", "号楼"),
            unit_no=self._property_part(unit_no, "单元"),
            room_no=self._property_part(room_no, "室", "号房"),
        )
        identity = self._identity(user)
        return identity, self._issue(identity)

    async def authenticate(self, token: str) -> AuthenticatedIdentity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["sub", "actor_type", "user_id", "iat", "exp", "token_id"]},
            )
            user_id = UUID(str(claims["user_id"]))
            if str(claims["sub"]) != str(user_id):
                raise AuthenticationError("TOKEN_INVALID")
            actor_type = ActorType(str(claims["actor_type"]))
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("TOKEN_EXPIRED") from exc
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("TOKEN_INVALID") from exc
        user = await self._repository.find_by_id(user_id)
        if user is None or not user.is_active or user.actor_type is not actor_type:
            raise AuthenticationError("TOKEN_INVALID")
        return self._identity(user)

    def hash_password(self, password: str) -> str:
        if len(password) < 8:
            raise ValueError("account password must contain at least 8 characters")
        return self._hasher.hash(password)

    def _verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def _issue(self, identity: AuthenticatedIdentity) -> AccessToken:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self._minutes)
        token = jwt.encode(
            {
                "sub": str(identity.user_id),
                "actor_type": identity.actor_type.value,
                "user_id": str(identity.user_id),
                "iat": now,
                "exp": expires_at,
                "token_id": str(uuid4()),
            },
            self._secret,
            algorithm=self._algorithm,
        )
        return AccessToken(token=token, expires_at=expires_at)

    @staticmethod
    def _property_part(value: str, *suffixes: str) -> str:
        normalized = value.strip()
        for suffix in suffixes:
            if normalized.endswith(suffix):
                return normalized[: -len(suffix)].strip()
        return normalized

    @staticmethod
    def _identity(user: AuthUser) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            user_id=user.user_id,
            username=user.username,
            actor_type=user.actor_type,
        )
