"""Offline formal qualification and publication CLI for hybrid interpretation."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from app.config import Settings
from app.llm.evaluation.hashing import sha256_value
from app.llm.hybrid.qualification import (
    publish_qualified_candidate,
    qualify_formal_reports,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fixflow-hybrid-qualify")
    root.add_argument("--regression-r1", type=Path, required=True)
    root.add_argument("--regression-r2", type=Path, required=True)
    root.add_argument("--challenge-r1", type=Path, required=True)
    root.add_argument("--challenge-r2", type=Path, required=True)
    root.add_argument("--output-directory", type=Path, required=True)
    root.add_argument("--allowed-root", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    qualification = qualify_formal_reports(
        (
            args.regression_r1,
            args.regression_r2,
            args.challenge_r1,
            args.challenge_r2,
        )
    )
    baseline, candidate = publish_qualified_candidate(
        qualification,
        output_directory=args.output_directory,
        allowed_root=args.allowed_root,
        endpoint_fingerprint=sha256_value(Settings().deepseek_base_url),
        sdk_version=f"httpx@{importlib.metadata.version('httpx')}",
    )
    print(
        json.dumps(
            {
                "qualification": qualification.status,
                "baseline_id": baseline.manifest.baseline_id,
                "baseline_hash": baseline.manifest_sha256,
                "release_candidate_id": candidate.release_candidate_id,
                "release_candidate_status": candidate.status,
                "activation": candidate.activation,
                "default_provider": candidate.default_provider,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
