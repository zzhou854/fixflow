"""Minimal FastAPI entry point for stage 0 environment verification."""

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app() -> FastAPI:
    application = FastAPI(title="FixFlow API", version="0.1.0")

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="fixflow-api")

    return application


app = create_app()
