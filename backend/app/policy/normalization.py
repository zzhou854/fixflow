"""Small deterministic normalization and hashing helpers."""

import hashlib
import json
import unicodedata


def normalize_policy_text(value: str) -> str:
    """Normalize only Unicode, case, and whitespace; no hidden synonyms."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_decision_value(value: str) -> str:
    return normalize_policy_text(value)


def stable_json_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
