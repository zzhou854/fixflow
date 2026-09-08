from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.time_resolution import resolve_common_daypart

REFERENCE = datetime(2026, 9, 9, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_resolves_common_relative_dayparts_in_resident_timezone() -> None:
    expected = {
        "明天上午家里有人": (9, 12),
        "明天下午方便": (14, 18),
        "后天晚上可以上门": (18, 21),
    }
    for message, hours in expected.items():
        windows = resolve_common_daypart(
            message,
            reference_time=REFERENCE,
            timezone_name="Asia/Shanghai",
        )
        assert windows is not None
        assert (windows[0].starts_at.hour, windows[0].ends_at.hour) == hours
        assert windows[0].starts_at.utcoffset() == REFERENCE.utcoffset()


def test_does_not_override_an_explicit_clock_time() -> None:
    assert (
        resolve_common_daypart(
            "明天上午十点以后在家",
            reference_time=REFERENCE,
            timezone_name="Asia/Shanghai",
        )
        is None
    )
