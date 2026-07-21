from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.domain.enums import ActorType


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(ApiModel):
    user_id: UUID
    username: str
    actor_type: ActorType


class LoginResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse
