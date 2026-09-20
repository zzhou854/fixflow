"""Versioned prompt specifications."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptSpec:
    prompt_name: str
    prompt_version: str
    system_instruction: str
    input_template: str
