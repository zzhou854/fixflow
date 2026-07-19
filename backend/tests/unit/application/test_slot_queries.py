"""Pure tests for the frozen deterministic slot-generation policy."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.application.errors import ApplicationError
from app.application.query_models import AvailableSlotReadModel, SlotWorkerSource, TimeWindow
from app.application.slot_queries import (
    SLOT_GRANULARITY_MINUTES,
    ceil_to_slot_boundary,
    generate_available_slots,
    normalize_service_area,
)
from app.domain.enums import WorkerSkillType

START = datetime(2030, 1, 1, 9, tzinfo=UTC)
END = datetime(2030, 1, 1, 12, tzinfo=UTC)


def _worker(
    worker_id: int,
    *,
    area: str = "Green Garden",
    workload: int = 0,
    availability: tuple[TimeWindow, ...] | None = None,
    bookings: tuple[TimeWindow, ...] = (),
) -> SlotWorkerSource:
    return SlotWorkerSource(
        worker_id=UUID(int=worker_id),
        worker_name=f"worker-{worker_id}",
        skill_type=WorkerSkillType.PLUMBING,
        service_area=area,
        open_ticket_count=workload,
        availability=availability or (TimeWindow(START, END),),
        booked_intervals=bookings,
    )


def _slots(
    *workers: SlotWorkerSource,
    start: datetime = START,
    end: datetime = END,
    duration: int = 60,
    maximum: int = 100,
) -> tuple[AvailableSlotReadModel, ...]:
    return generate_available_slots(
        sources=tuple(workers),
        target_service_area="Green Garden",
        search_window_start=start,
        search_window_end=end,
        requested_duration_minutes=duration,
        max_results=maximum,
    )


def test_slot_granularity_and_natural_clock_ceiling_are_frozen() -> None:
    assert SLOT_GRANULARITY_MINUTES == 30
    assert ceil_to_slot_boundary(START + timedelta(minutes=10)) == START + timedelta(minutes=30)
    assert ceil_to_slot_boundary(START + timedelta(minutes=30)) == START + timedelta(minutes=30)
    assert ceil_to_slot_boundary(START + timedelta(minutes=30, seconds=1)) == START + timedelta(
        hours=1
    )


def test_duration_is_exact_and_slots_stay_inside_search_window() -> None:
    rows = _slots(
        _worker(1),
        start=START + timedelta(minutes=10),
        end=START + timedelta(hours=2, minutes=10),
        duration=45,
    )
    assert rows[0].scheduled_start == START + timedelta(minutes=30)
    assert all(row.scheduled_end - row.scheduled_start == timedelta(minutes=45) for row in rows)
    assert all(row.scheduled_end <= START + timedelta(hours=2, minutes=10) for row in rows)


def test_incomplete_availability_does_not_cover_candidate() -> None:
    worker = _worker(
        1,
        availability=(TimeWindow(START + timedelta(minutes=30), START + timedelta(hours=1)),),
    )
    assert _slots(worker, start=START, end=START + timedelta(hours=1), duration=60) == ()


def test_overlapping_booked_interval_is_removed() -> None:
    worker = _worker(
        1,
        bookings=(TimeWindow(START + timedelta(minutes=30), START + timedelta(hours=1)),),
    )
    starts = [row.scheduled_start for row in _slots(worker, duration=30)]
    assert START + timedelta(minutes=30) not in starts
    assert START in starts and START + timedelta(hours=1) in starts


def test_service_area_is_normalized_exact_hard_constraint() -> None:
    matching = _worker(1, area="  GREEN   garden ")
    outside = _worker(2, area="Other Garden")
    rows = _slots(matching, outside)
    assert {row.worker_id for row in rows} == {matching.worker_id}
    assert all(row.service_area_matched for row in rows)
    assert normalize_service_area("  GREEN   garden ") == "green garden"


def test_stable_order_is_start_then_workload_then_worker_id() -> None:
    busy_low_id = _worker(1, workload=3)
    free_high_id = _worker(3, workload=0)
    free_low_id = _worker(2, workload=0)
    rows = _slots(busy_low_id, free_high_id, free_low_id, end=START + timedelta(hours=1))
    first_start = [row for row in rows if row.scheduled_start == START]
    assert [row.worker_id for row in first_start] == [UUID(int=2), UUID(int=3), UUID(int=1)]
    assert rows == _slots(free_high_id, busy_low_id, free_low_id, end=START + timedelta(hours=1))


def test_earlier_candidate_always_precedes_later_lower_workload() -> None:
    early_busy = _worker(1, workload=10)
    late_free = _worker(
        2,
        availability=(TimeWindow(START + timedelta(minutes=30), END),),
        workload=0,
    )
    rows = _slots(early_busy, late_free, end=START + timedelta(hours=1))
    assert rows[0].worker_id == early_busy.worker_id
    assert rows[0].scheduled_start == START


def test_max_results_and_contiguous_ranks_are_enforced() -> None:
    rows = _slots(_worker(1), _worker(2), maximum=3, duration=30)
    assert len(rows) == 3
    assert [row.rank for row in rows] == [1, 2, 3]
    assert all(row.slot_granularity_minutes == 30 for row in rows)


@pytest.mark.parametrize("field_value", [datetime(2030, 1, 1, 9), datetime(2030, 1, 1, 12)])
def test_timezone_naive_values_are_rejected(field_value: datetime) -> None:
    with pytest.raises(ApplicationError, match="timezone_required"):
        _slots(_worker(1), start=field_value)


def test_no_candidate_is_an_empty_tuple_not_an_error() -> None:
    assert _slots(_worker(1, area="unsupported")) == ()


def test_exclusive_availability_lower_bound_is_honored() -> None:
    worker = _worker(1, availability=(TimeWindow(START, END, lower_inclusive=False),))
    rows = _slots(worker, duration=30)
    assert rows[0].scheduled_start == START + timedelta(minutes=30)


def test_duplicate_availability_windows_do_not_duplicate_candidates() -> None:
    window = TimeWindow(START, END)
    rows = _slots(_worker(1, availability=(window, replace(window))))
    assert len({(row.worker_id, row.scheduled_start) for row in rows}) == len(rows)
