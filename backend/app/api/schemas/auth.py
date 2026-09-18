from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.domain.enums import ActorType


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class RegisterResidentRequest(ApiModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=256)
    community_name: str = Field(min_length=1, max_length=100)
    building_no: str = Field(min_length=1, max_length=30)
    unit_no: str = Field(min_length=1, max_length=30)
    room_no: str = Field(min_length=1, max_length=30)


class UserResponse(ApiModel):
    user_id: UUID
    username: str
    actor_type: ActorType


class LoginResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse
