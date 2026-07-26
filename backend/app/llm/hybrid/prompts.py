"""Content-addressed fact-extraction prompt registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.llm.hybrid.models import ExtractedResidentFactsV1


class FactPromptDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    prompt_version: str
    schema_version: str
    system_template: str
    output_schema: dict[str, object]
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FactPromptRegistry:
    PROMPT_ID = "resident_fact_extraction"
    DEFAULT_VERSION = "1.0.0"
    SCHEMA_VERSION = "resident-facts-v1"
    SUPPORTED_VERSIONS = ("1.0.0",)

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(__file__).parents[1] / "prompts" / self.PROMPT_ID
        self._definitions: dict[str, FactPromptDefinition] = {}

    def resident_fact_extraction(self, version: str | None = None) -> FactPromptDefinition:
        selected = version or self.DEFAULT_VERSION
        if selected not in self.SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported resident fact prompt version: {selected}")
        if selected in self._definitions:
            return self._definitions[selected]
        system = (self._root / "system_v1.md").read_text(encoding="utf-8")
        schema = ExtractedResidentFactsV1.model_json_schema(mode="validation")
        canonical = json.dumps(
            {
                "prompt_id": self.PROMPT_ID,
                "prompt_version": selected,
                "schema_version": self.SCHEMA_VERSION,
                "system_template": system,
                "output_schema": schema,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        definition = FactPromptDefinition(
            prompt_id=self.PROMPT_ID,
            prompt_version=selected,
            schema_version=self.SCHEMA_VERSION,
            system_template=system,
            output_schema=schema,
            prompt_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        )
        self._definitions[selected] = definition
        return definition
