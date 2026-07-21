"""ASGI entry point for FixFlow."""

from app.api.app import create_app
from app.config import get_settings

app = create_app(cors_origins=get_settings().cors_origins)
