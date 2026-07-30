from __future__ import annotations

import argparse
import asyncio

import pytest
from app.llm.hybrid import cli


def test_hybrid_cli_accepts_explicit_routed_development_mode(tmp_path: object) -> None:
    args = cli.parser().parse_args(
        [
            "--stage",
            "development",
            "--model",
            "flash-to-pro",
            "--output",
            "report.json",
            "--allow-network",
            "--acknowledge-cost",
        ]
    )
    assert args.model == "flash-to-pro"


def test_hybrid_cli_requires_live_provider_environment_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisabledSettings:
        enable_live_provider_tests = False
        deepseek_api_key = object()

    monkeypatch.setattr(cli, "Settings", lambda **_kwargs: DisabledSettings())
    args = argparse.Namespace(
        stage="development",
        runtime_commit=None,
        model="deepseek-v4-flash",
    )
    with pytest.raises(SystemExit, match="FIXFLOW_ENABLE_LIVE_PROVIDER_TESTS"):
        asyncio.run(cli._run(args))
