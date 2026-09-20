"""Deterministic interpretation for common resident availability phrases."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

from app.agent.models import TimeWindow

COMMON_DAYPART_HOURS = MappingProxyType(
    {
        "上午": (9, 12),
        "下午": (14, 18),
        "晚上": (18, 21),
    }
)

_RELATIVE_DAYS = MappingProxyType({"今天": 0, "今日": 0, "明天": 1, "后天": 2})
_EXPLICIT_CLOCK = re.compile(r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:[:：点时])")


def resolve_common_daypart(
    message: str,
    *,
    reference_time: datetime,
    timezone_name: str,
) -> tuple[TimeWindow, ...] | None:
    """Resolve an unambiguous relative-day + daypart phrase.

    Exact clock expressions and mixed dayparts remain provider-owned. This small
    deterministic layer prevents phrases such as ``明天上午`` from becoming
    midnight windows while preserving the resident's stated precision.
    """

    if _EXPLICIT_CLOCK.search(message):
        return None
    day_matches = [value for marker, value in _RELATIVE_DAYS.items() if marker in message]
    part_matches = [part for part in COMMON_DAYPART_HOURS if part in message]
    if len(set(day_matches)) != 1 or len(part_matches) != 1:
        return None

    local_reference = reference_time.astimezone(ZoneInfo(timezone_name))
    target_date = local_reference.date() + timedelta(days=day_matches[0])
    start_hour, end_hour = COMMON_DAYPART_HOURS[part_matches[0]]
    timezone = ZoneInfo(timezone_name)
    return (
        TimeWindow(
            starts_at=datetime.combine(target_date, time(start_hour), timezone),
            ends_at=datetime.combine(target_date, time(end_hour), timezone),
        ),
    )
