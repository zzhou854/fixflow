"""Strict JSON corpus loading; no arbitrary fields or executable content."""

import json
from pathlib import Path

from app.policy.models import PolicyCorpus


def load_policy_corpus(path: Path) -> PolicyCorpus:
    return PolicyCorpus.model_validate(json.loads(path.read_text(encoding="utf-8")))
