"""Pure deterministic candidate-slot generation and ordering."""

from datetime import datetime, timedelta

from app.application.errors import ApplicationError
from app.application.query_models import AvailableSlotReadModel, SlotWorkerSource, TimeWindow

SLOT_GRANULARITY_MINUTES = 30
MAX_SLOT_RESULTS = 100


def normalize_service_area(value: str) -> str:
    """Normalize only deterministic formatting differences."""

    return " ".join(value.strip().casefold().split())


def _ensure_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationError("timezone_required", field=field)


def validate_slot_query(
    start: datetime,
    end: datetime,
    duration_minutes: int,
    max_results: int,
) -> None:
    _ensure_aware(start, field="search_window_start")
    _ensure_aware(end, field="search_window_end")
    if end <= start:
        raise ApplicationError("invalid_search_window")
    if duration_minutes <= 0:
        raise ApplicationError("invalid_requested_duration")
    if not 1 <= max_results <= MAX_SLOT_RESULTS:
        raise ApplicationError("invalid_max_results", maximum=MAX_SLOT_RESULTS)


def ceil_to_slot_boundary(value: datetime) -> datetime:
    """Move a timestamp forward to its local natural-clock 30-minute boundary."""

    _ensure_aware(value, field="search_window_start")
    base = value.replace(second=0, microsecond=0)
    remainder = base.minute % SLOT_GRANULARITY_MINUTES
    if remainder == 0 and value.second == 0 and value.microsecond == 0:
        return base
    minutes = SLOT_GRANULARITY_MINUTES - remainder if remainder else SLOT_GRANULARITY_MINUTES
    return base + timedelta(minutes=minutes)


def _covered(candidate_start: datetime, candidate_end: datetime, window: TimeWindow) -> bool:
    if candidate_start < window.starts_at or candidate_end > window.ends_at:
        return False
    return not (candidate_start == window.starts_at and not window.lower_inclusive)


def _overlaps(candidate_start: datetime, candidate_end: datetime, window: TimeWindow) -> bool:
    return candidate_start < window.ends_at and candidate_end > window.starts_at


def generate_available_slots(
    *,
    sources: tuple[SlotWorkerSource, ...],
    target_service_area: str,
    search_window_start: datetime,
    search_window_end: datetime,
    requested_duration_minutes: int,
    max_results: int,
) -> tuple[AvailableSlotReadModel, ...]:
    """Generate stable candidates from one current database snapshot."""

    validate_slot_query(
        search_window_start,
        search_window_end,
        requested_duration_minutes,
        max_results,
    )
    target_area = normalize_service_area(target_service_area)
    duration = timedelta(minutes=requested_duration_minutes)
    first_start = ceil_to_slot_boundary(search_window_start)
    candidates: list[AvailableSlotReadModel] = []
    seen: set[tuple[object, datetime]] = set()
    for worker in sources:
        if normalize_service_area(worker.service_area) != target_area:
            continue
        candidate_start = first_start
        while candidate_start + duration <= search_window_end:
            candidate_end = candidate_start + duration
            identity = (worker.worker_id, candidate_start)
            if (
                identity not in seen
                and any(
                    _covered(candidate_start, candidate_end, window)
                    for window in worker.availability
                )
                and not any(
                    _overlaps(candidate_start, candidate_end, booking)
                    for booking in worker.booked_intervals
                )
            ):
                seen.add(identity)
                candidates.append(
                    AvailableSlotReadModel(
                        worker_id=worker.worker_id,
                        worker_name=worker.worker_name,
                        skill_type=worker.skill_type,
                        service_area=worker.service_area,
                        service_area_matched=True,
                        scheduled_start=candidate_start,
                        scheduled_end=candidate_end,
                        open_ticket_count=worker.open_ticket_count,
                        rank=0,
                        slot_granularity_minutes=SLOT_GRANULARITY_MINUTES,
                    )
                )
            candidate_start += timedelta(minutes=SLOT_GRANULARITY_MINUTES)
    ordered = sorted(
        candidates,
        key=lambda item: (item.scheduled_start, item.open_ticket_count, item.worker_id),
    )[:max_results]
    return tuple(
        AvailableSlotReadModel(
            worker_id=item.worker_id,
            worker_name=item.worker_name,
            skill_type=item.skill_type,
            service_area=item.service_area,
            service_area_matched=item.service_area_matched,
            scheduled_start=item.scheduled_start,
            scheduled_end=item.scheduled_end,
            open_ticket_count=item.open_ticket_count,
            rank=index,
            slot_granularity_minutes=item.slot_granularity_minutes,
        )
        for index, item in enumerate(ordered, start=1)
    )
