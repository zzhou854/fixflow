from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.llm.online.live_smoke import (
    _verify_deterministic_fallback,
    _verify_mock_route,
    run_live_smoke,
)


@pytest.mark.asyncio
async def test_live_smoke_is_disabled_by_default(tmp_path: Path) -> None:
    settings = Settings(
        llm_provider="scripted",
        enable_live_provider_tests=False,
    )
    with pytest.raises(RuntimeError, match="explicitly set"):
        await run_live_smoke(settings, output_path=tmp_path / "smoke.json")


@pytest.mark.asyncio
async def test_mock_route_reaches_pro_without_real_provider_call() -> None:
    assert await _verify_mock_route()


@pytest.mark.asyncio
async def test_grounded_failure_uses_deterministic_fallback() -> None:
    assert await _verify_deterministic_fallback()
