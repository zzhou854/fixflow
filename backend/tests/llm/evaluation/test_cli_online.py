from pathlib import Path

import pytest
from app.llm.evaluation.artifacts import ARTIFACT_FILES
from app.llm.evaluation.cli import build_parser, main
from app.llm.evaluation.errors import EvaluationExitCode, OnlineGuardError
from app.llm.evaluation.online_guard import require_online_authorization
from tests.llm.evaluation.helpers import report


@pytest.mark.parametrize(
    ("allow_network", "acknowledge_cost", "api_key_available", "message"),
    [
        (False, False, False, "--allow-network"),
        (True, False, False, "--acknowledge-cost"),
        (True, True, False, "GLM_API_KEY"),
    ],
)
def test_online_guard_rejects_before_client_construction(
    allow_network: bool,
    acknowledge_cost: bool,
    api_key_available: bool,
    message: str,
) -> None:
    constructed = 0
    with pytest.raises(OnlineGuardError, match=message):
        require_online_authorization(
            provider="glm",
            allow_network=allow_network,
            acknowledge_cost=acknowledge_cost,
            api_key_available=api_key_available,
        )
        constructed += 1
    assert constructed == 0


def test_scripted_mode_needs_no_online_flags() -> None:
    require_online_authorization(
        provider="scripted",
        allow_network=False,
        acknowledge_cost=False,
        api_key_available=False,
    )


def test_cli_has_no_api_key_argument() -> None:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["run", "--provider", "glm", "--api-key", "forbidden"])
    assert caught.value.code == 2


def test_cli_allows_explicit_prompt_v2_only_for_internal_evaluation() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--provider",
            "fake-glm",
            "--prompt-version",
            "2.0.0",
        ]
    )
    assert args.prompt_version == "2.0.0"


def test_prompt_development_rejects_challenge_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.evaluation.cli._provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be constructed")
        ),
    )
    path = (
        Path(__file__).resolve().parents[3]
        / "evals"
        / "datasets"
        / "resident_interpretation_challenge_v1.jsonl"
    )
    assert (
        main(
            [
                "run",
                "--provider",
                "glm",
                "--dataset",
                str(path),
                "--prompt-version",
                "2.0.0",
                "--run-purpose",
                "prompt-development",
                "--development-source-fingerprint",
                "f" * 64,
                "--allow-network",
                "--acknowledge-cost",
                "--allow-dirty",
            ]
        )
        == EvaluationExitCode.ONLINE_GUARD_FAILED
    )


def test_unknown_cli_command_is_rejected() -> None:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["unknown-command"])
    assert caught.value.code == 2


def test_validate_and_inspect_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-dataset"]) == EvaluationExitCode.SUCCESS
    assert "cases=120" in capsys.readouterr().out
    assert main(["inspect"]) == EvaluationExitCode.SUCCESS
    assert "suite_distribution" in capsys.readouterr().out


def test_hash_dataset_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hash-dataset"]) == EvaluationExitCode.SUCCESS
    assert (
        capsys.readouterr().out.strip()
        == "a0dfb91aed5653eafcb5649beee1f9106ac4d6b332df50923c68d5f02e979296"
    )


def test_online_cli_without_network_flag_returns_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.llm.evaluation.cli._api_key_available", lambda: True)
    assert main(["run", "--provider", "glm"]) == EvaluationExitCode.ONLINE_GUARD_FAILED


def test_online_cli_without_cost_ack_returns_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.llm.evaluation.cli._api_key_available", lambda: True)
    assert (
        main(["run", "--provider", "glm", "--allow-network"])
        == EvaluationExitCode.ONLINE_GUARD_FAILED
    )


def test_online_cli_without_key_returns_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.llm.evaluation.cli._api_key_available", lambda: False)
    assert (
        main(
            [
                "run",
                "--provider",
                "glm",
                "--allow-network",
                "--acknowledge-cost",
            ]
        )
        == EvaluationExitCode.ONLINE_GUARD_FAILED
    )


def test_fake_glm_cli_runs_offline_through_production_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    output = tmp_path / "fake-glm"
    assert (
        main(["run", "--provider", "fake-glm", "--output", str(output)])
        == EvaluationExitCode.GATE_FAILED
    )
    assert set(ARTIFACT_FILES) == {path.name for path in output.iterdir()}


def test_compare_cli_compatible(tmp_path: Path) -> None:
    baseline, candidate = report(run_int=1), report(run_int=2, model="candidate")
    baseline_path, candidate_path = tmp_path / "base.json", tmp_path / "candidate.json"
    baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")
    candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    output = tmp_path / "comparison.json"
    assert (
        main(
            [
                "compare",
                "--baseline",
                str(baseline_path),
                "--candidate",
                str(candidate_path),
                "--output",
                str(output),
            ]
        )
        == EvaluationExitCode.SUCCESS
    )
    assert output.exists()


def test_compare_cli_incompatible_returns_five(tmp_path: Path) -> None:
    baseline = report(run_int=1)
    candidate = report(run_int=2, dataset_version="2.0.0")
    baseline_path, candidate_path = tmp_path / "base.json", tmp_path / "candidate.json"
    baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")
    candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    assert (
        main(
            [
                "compare",
                "--baseline",
                str(baseline_path),
                "--candidate",
                str(candidate_path),
                "--output",
                str(tmp_path / "comparison.json"),
            ]
        )
        == EvaluationExitCode.INCOMPATIBLE
    )


def test_gate_cli_pass_and_fail(
    tmp_path: Path,
    gate_policy: object,
) -> None:
    del gate_policy
    passing = tmp_path / "passing.json"
    passing.write_text(report().model_dump_json(), encoding="utf-8")
    assert main(["gate", "--report", str(passing)]) == EvaluationExitCode.SUCCESS
    failing = tmp_path / "failing.json"
    failing.write_text(
        report(
            metrics=report().metrics.model_copy(update={"critical_safety_recall": 0.9})
        ).model_dump_json(),
        encoding="utf-8",
    )
    assert main(["gate", "--report", str(failing)]) == EvaluationExitCode.GATE_FAILED


def test_invalid_dataset_path_returns_two(tmp_path: Path) -> None:
    assert (
        main(["validate-dataset", "--dataset", str(tmp_path / "missing.jsonl")])
        == EvaluationExitCode.INVALID_INPUT
    )
