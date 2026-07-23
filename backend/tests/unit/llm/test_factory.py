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
