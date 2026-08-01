import pytest
from app.config import Settings
from app.llm import factory


def test_default_scripted_factory_never_constructs_online_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted = object()

    def forbidden(**_: object) -> object:
        raise AssertionError("online provider must not be constructed")

    monkeypatch.setattr(factory, "ZaiGLMStructuredInterpretationProvider", forbidden)
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://u:p@localhost/db",
            "llm_provider": "scripted",
        }
    )
    result = factory.build_structured_interpretation_provider(
        settings,
        scripted_provider=scripted,  # type: ignore[arg-type]
    )
    assert result is scripted


def test_evaluation_can_inject_prompt_v2_without_changing_runtime_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    class CapturingProvider:
        def __init__(self, **kwargs: object) -> None:
            prompt = kwargs["prompt"]
            prompts.append(prompt.prompt_version)  # type: ignore[attr-defined]

    monkeypatch.setattr(factory, "ZaiGLMStructuredInterpretationProvider", CapturingProvider)
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://u:p@localhost/db",
            "llm_provider": "glm",
            "glm_api_key": "synthetic-test-secret-value",
            "llm_online_enabled": False,
            "online_canary_enabled": False,
            "online_structured_understanding_enabled": False,
            "online_grounded_response_enabled": False,
        }
    )
    factory.build_structured_interpretation_provider(
        settings,
        scripted_provider=object(),  # type: ignore[arg-type]
        prompt_version="2.0.0",
    )
    assert prompts == ["2.0.0"]


def test_factory_constructs_deepseek_without_changing_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        factory,
        "DeepSeekStructuredInterpretationProvider",
        CapturingProvider,
    )
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://u:p@localhost/db",
            "llm_provider": "deepseek",
            "deepseek_api_key": "synthetic-test-secret-value",
            "llm_online_enabled": False,
            "online_canary_enabled": False,
            "online_structured_understanding_enabled": False,
            "online_grounded_response_enabled": False,
        }
    )
    factory.build_structured_interpretation_provider(
        settings,
        scripted_provider=object(),  # type: ignore[arg-type]
        prompt_version="2.0.0",
        provider_max_attempts=1,
        provider_max_concurrency=1,
    )
    config = captured["config"]
    assert config.model == "deepseek-v4-flash"  # type: ignore[attr-defined]
    assert config.thinking_mode == "disabled"  # type: ignore[attr-defined]
    assert config.max_attempts == 1  # type: ignore[attr-defined]
    assert config.max_concurrency == 1  # type: ignore[attr-defined]
