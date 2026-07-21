from fastapi import APIRouter, Depends

from app.api.dependencies import ApiServices, get_identity, get_services
from app.api.schemas.auth import LoginRequest, LoginResponse, UserResponse
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


@router.get("/me", response_model=UserResponse)
async def me(identity: AuthenticatedIdentity = Depends(get_identity)) -> UserResponse:
    return UserResponse.model_validate(identity)
