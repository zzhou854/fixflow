"""Validated, content-addressed prompt registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.models import InterpretMessageOutput


class PromptExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    input: dict[str, object]
    output: InterpretMessageOutput


class PromptDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    prompt_version: str
    schema_version: str
    system_template: str
    output_schema: dict[str, object]
    examples: tuple[PromptExample, ...]
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


EXAMPLE_ADAPTER = TypeAdapter(tuple[PromptExample, ...])


class PromptRegistry:
    PROMPT_ID = "resident_interpretation"
    PROMPT_VERSION = "1.0.0"
    SCHEMA_VERSION = "interpretation-result-v1"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(__file__).parent / self.PROMPT_ID
        self._definition: PromptDefinition | None = None

    def resident_interpretation(self) -> PromptDefinition:
        if self._definition is not None:
            return self._definition
        system = (self._root / "system_v1.md").read_text(encoding="utf-8")
        schema = json.loads((self._root / "output_schema_v1.json").read_text(encoding="utf-8"))
        examples_raw = json.loads((self._root / "examples_v1.json").read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError("prompt output schema must be an object")
        examples = EXAMPLE_ADAPTER.validate_python(examples_raw)
        if not 12 <= len(examples) <= 20:
            raise ValueError("resident interpretation prompt must contain 12 to 20 examples")
        canonical = json.dumps(
            {
                "prompt_id": self.PROMPT_ID,
                "prompt_version": self.PROMPT_VERSION,
                "schema_version": self.SCHEMA_VERSION,
                "system_template": system,
                "output_schema": schema,
                "examples": [example.model_dump(mode="json") for example in examples],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._definition = PromptDefinition(
            prompt_id=self.PROMPT_ID,
            prompt_version=self.PROMPT_VERSION,
            schema_version=self.SCHEMA_VERSION,
            system_template=system,
            output_schema=schema,
            examples=examples,
            prompt_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return self._definition
