"""Platform-safe development entry point for the FixFlow API host."""

from __future__ import annotations

import asyncio
import os

import uvicorn

from app.agent_runtime.initialize_checkpoints import configure_windows_selector_loop


def selector_event_loop() -> asyncio.AbstractEventLoop:
    """Create the psycopg-compatible event loop expected by Uvicorn."""

    return asyncio.SelectorEventLoop()


def main() -> None:
    configure_windows_selector_loop()
    uvicorn.run(
        "app.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
        loop="app.api.run:selector_event_loop",
    )


if __name__ == "__main__":
    main()
