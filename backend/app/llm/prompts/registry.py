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
    DEFAULT_PROMPT_VERSION = "1.0.0"
    SCHEMA_VERSION = "interpretation-result-v1"
    SUPPORTED_VERSIONS = (
        "1.0.0",
        "2.0.0",
        "2.1.0",
        "2.2.0",
        "3.0.0",
        "3.1.0",
        "3.2.0",
        "3.3.0",
        "3.4.0",
        "3.5.0",
        "3.6.0",
        "3.7.0",
        "4.0.0",
    )

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(__file__).parent / self.PROMPT_ID
        self._definitions: dict[str, PromptDefinition] = {}

    def resident_interpretation(
        self,
        version: str | None = None,
    ) -> PromptDefinition:
        selected = version or self.DEFAULT_PROMPT_VERSION
        if selected not in self.SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported resident interpretation prompt version: {selected}")
        if selected in self._definitions:
            return self._definitions[selected]
        asset_version = selected.split(".", maxsplit=1)[0]
        system = (self._root / f"system_v{asset_version}.md").read_text(encoding="utf-8")
        schema = json.loads((self._root / "output_schema_v1.json").read_text(encoding="utf-8"))
        examples_asset_version = "2" if selected.startswith(("3.", "4.")) else asset_version
        examples_raw = json.loads(
            (self._root / f"examples_v{examples_asset_version}.json").read_text(encoding="utf-8")
        )
        if selected in {"2.1.0", "2.2.0"}:
            appendix = "system_v2_1_append.md" if selected == "2.1.0" else "system_v2_2_append.md"
            system += "\n\n" + (self._root / appendix).read_text(encoding="utf-8")
            examples_appendix = (
                "examples_v3_5_append.json"
                if selected in {"3.5.0", "3.6.0", "3.7.0"}
                else "examples_v2_1_append.json"
            )
            examples_raw += json.loads((self._root / examples_appendix).read_text(encoding="utf-8"))
        elif selected.startswith("3."):
            examples_raw += json.loads(
                (self._root / "examples_v2_1_append.json").read_text(encoding="utf-8")
            )
            if selected == "3.1.0":
                system += "\n\n" + (self._root / "system_v3_1_append.md").read_text(
                    encoding="utf-8"
                )
            if selected == "3.2.0":
                system += "\n\n" + (self._root / "system_v3_2_append.md").read_text(
                    encoding="utf-8"
                )
            if selected == "3.3.0":
                system += "\n\n" + (self._root / "system_v3_1_append.md").read_text(
                    encoding="utf-8"
                )
                system += "\n\n" + (self._root / "system_v3_3_append.md").read_text(
                    encoding="utf-8"
                )
            if selected == "3.4.0":
                system += "\n\n" + (self._root / "system_v3_4_append.md").read_text(
                    encoding="utf-8"
                )
            if selected == "3.5.0":
                system += "\n\n" + (self._root / "system_v3_4_append.md").read_text(
                    encoding="utf-8"
                )
            if selected == "3.6.0":
                system += "\n\n" + (self._root / "system_v3_6_append.md").read_text(
                    encoding="utf-8"
                )
            if selected == "3.7.0":
                system += "\n\n" + (self._root / "system_v3_7_append.md").read_text(
                    encoding="utf-8"
                )
        if not isinstance(schema, dict):
            raise ValueError("prompt output schema must be an object")
        examples = EXAMPLE_ADAPTER.validate_python(examples_raw)
        minimum, maximum = (12, 20) if selected == "1.0.0" else (20, 24)
        if not minimum <= len(examples) <= maximum:
            raise ValueError(
                f"resident interpretation prompt {selected} must contain "
                f"{minimum} to {maximum} examples"
            )
        canonical = json.dumps(
            {
                "prompt_id": self.PROMPT_ID,
                "prompt_version": selected,
                "schema_version": self.SCHEMA_VERSION,
                "system_template": system,
                "output_schema": schema,
                "examples": [example.model_dump(mode="json") for example in examples],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        definition = PromptDefinition(
            prompt_id=self.PROMPT_ID,
            prompt_version=selected,
            schema_version=self.SCHEMA_VERSION,
            system_template=system,
            output_schema=schema,
            examples=examples,
            prompt_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self._definitions[selected] = definition
        return definition
