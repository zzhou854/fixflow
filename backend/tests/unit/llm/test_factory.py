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
        }
    )
    factory.build_structured_interpretation_provider(
        settings,
        scripted_provider=object(),  # type: ignore[arg-type]
        prompt_version="2.0.0",
    )
    assert prompts == ["2.0.0"]
