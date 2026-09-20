from fastapi import APIRouter, Depends

from app.api.dependencies import ApiServices, get_identity, get_services
from app.api.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RegisterResidentRequest,
    UserResponse,
)
from app.application.auth import AuthenticatedIdentity

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest, services: ApiServices = Depends(get_services)
) -> LoginResponse:
    identity, token = await services.auth.login(request.username, request.password)
    return LoginResponse(
        access_token=token.token,
        expires_at=token.expires_at,
        user=UserResponse.model_validate(identity),
    )


@router.post("/register/resident", response_model=LoginResponse, status_code=201)
async def register_resident(
    request: RegisterResidentRequest, services: ApiServices = Depends(get_services)
) -> LoginResponse:
    identity, token = await services.auth.register_resident(
        request.username,
        request.password,
        request.community_name,
        request.building_no,
        request.unit_no,
        request.room_no,
    )
    return LoginResponse(
        access_token=token.token,
        expires_at=token.expires_at,
        user=UserResponse.model_validate(identity),
    )


@router.get("/me", response_model=UserResponse)
async def me(identity: AuthenticatedIdentity = Depends(get_identity)) -> UserResponse:
    return UserResponse.model_validate(identity)
