"""Independent, single-use cursors over a validated replay tape."""

from __future__ import annotations

from app.replay.enums import ReplayMismatchType, ReplayStepKind
from app.replay.models import ReplayMismatch
from app.replay.steps import ReplayStepPayload, ReplayStepRecord


class ReplayTapeError(RuntimeError):
    code = "REPLAY_TAPE_ERROR"


class ReplayTapeMiss(ReplayTapeError):
    code = "REPLAY_TAPE_MISS"


class ReplayTapeCursor:
    def __init__(self, steps: tuple[ReplayStepRecord, ...]) -> None:
        self._steps = steps
        self._index = 0
        self.mismatches: list[ReplayMismatch] = []

    @property
    def consumed(self) -> int:
        return self._index

    @property
    def total(self) -> int:
        return len(self._steps)

    @property
    def remaining(self) -> int:
        return len(self._steps) - self._index

    @property
    def next_kind(self) -> ReplayStepKind | None:
        return self._steps[self._index].step_kind if self._index < len(self._steps) else None

    def consume(
        self,
        kind: ReplayStepKind,
        *,
        request_fingerprint: str | None = None,
        step_key: str | None = None,
    ) -> ReplayStepPayload:
        if self._index >= len(self._steps):
            self.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.REPLAY_TAPE_MISS,
                    step_key=step_key,
                    expected_summary="end-of-tape",
                    actual_summary=kind.value,
                )
            )
            raise ReplayTapeMiss(f"missing replay step {kind.value}")
        expected = self._steps[self._index]
        self._index += 1
        if expected.step_kind is not kind:
            self.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.UNEXPECTED_REPLAY_CALL,
                    step_key=expected.step_key,
                    expected_summary=expected.step_kind.value,
                    actual_summary=kind.value,
                )
            )
            raise ReplayTapeMiss(f"expected {expected.step_kind.value}, received {kind.value}")
        if step_key is not None and expected.step_key != step_key:
            self.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.EXTERNAL_CALL_ORDER_MISMATCH,
                    step_key=expected.step_key,
                    expected_summary=expected.step_key,
                    actual_summary=step_key,
                )
            )
        if (
            request_fingerprint is not None
            and expected.request_fingerprint is not None
            and expected.request_fingerprint != request_fingerprint
        ):
            self.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.REQUEST_FINGERPRINT_MISMATCH,
                    step_key=expected.step_key,
                    expected_summary=expected.request_fingerprint,
                    actual_summary=request_fingerprint,
                )
            )
        return expected.payload

    def finish(self) -> None:
        for step in self._steps[self._index :]:
            self.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.UNCONSUMED_REPLAY_STEP,
                    step_key=step.step_key,
                    expected_summary=step.step_kind.value,
                    actual_summary="not consumed",
                )
            )
        self._index = len(self._steps)
