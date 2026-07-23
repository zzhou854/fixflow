"""Run identities and best-effort source revision evidence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from app.llm.evaluation.hashing import sha256_value
from app.llm.evaluation.models import EvaluationProviderConfiguration


@dataclass(frozen=True, slots=True)
class GitIdentity:
    commit: str | None
    dirty: bool | None


def read_git_identity() -> GitIdentity:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        return GitIdentity(commit=commit, dirty=bool(status.strip()))
    except (OSError, subprocess.SubprocessError):
        return GitIdentity(commit=None, dirty=None)


def settings_fingerprint(configuration: EvaluationProviderConfiguration) -> str:
    payload = configuration.model_dump(
        mode="json",
        exclude={"endpoint_fingerprint", "provider_sdk_version", "live_network"},
    )
    return sha256_value(payload)
