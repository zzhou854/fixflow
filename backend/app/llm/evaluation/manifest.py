"""Run identities and best-effort source revision evidence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def source_tree_fingerprint(paths: tuple[Path, ...]) -> str:
    """Hash selected source content without paths outside the repository or mtimes."""

    payload: dict[str, str] = {}
    root = Path.cwd().resolve()
    for path in sorted((item.resolve() for item in paths), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        payload[relative] = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return sha256_value(payload)


def development_source_fingerprint(
    *,
    head_commit: str,
    prompt_hash: str,
    challenge_dataset_hash: str,
    scorer_identity: str,
    policy_identity: str,
    git_diff_hash: str,
) -> str:
    return sha256_value(
        {
            "head_commit": head_commit,
            "prompt_hash": prompt_hash,
            "challenge_dataset_hash": challenge_dataset_hash,
            "scorer_identity": scorer_identity,
            "policy_identity": policy_identity,
            "git_diff_hash": git_diff_hash,
        }
    )
