from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.scheduling.internal.calendar import CronCalendar, due_times, normalize_schedule


def test_cron_iana_timezone_and_numeric_ranges():
    calendar = CronCalendar.parse("*/15 9-10 * * 1-5")
    after = datetime(2026, 10, 30, 2, 45, tzinfo=UTC)  # Friday 10:45 in Shanghai
    assert calendar.next_after(after, "Asia/Shanghai") == datetime(2026, 11, 2, 1, tzinfo=UTC)
    # Restricted day of month and weekday use cron's inclusive OR.
    assert CronCalendar.parse("0 9 1 * 1").next_after(
        datetime(2026, 10, 31, tzinfo=UTC), "UTC"
    ) == datetime(2026, 11, 1, 9, tzinfo=UTC)


def test_cron_dst_skips_gap_and_emits_both_fold_instants():
    spring = CronCalendar.parse("30 2 * * *")
    assert spring.next_after(datetime(2026, 3, 8, 5, tzinfo=UTC), "America/New_York") == datetime(
        2026, 3, 9, 6, 30, tzinfo=UTC
    )
    autumn = CronCalendar.parse("30 1 * * *")
    first = autumn.next_after(datetime(2026, 11, 1, 4, tzinfo=UTC), "America/New_York")
    second = autumn.next_after(first, "America/New_York")
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second == first + timedelta(hours=1)


@pytest.mark.parametrize(
    "expression", ["* * * * * *", "61 * * * *", "*/0 * * * *", "@daily", "0 9 * * MON"]
)
def test_unsupported_cron_rejected(expression):
    with pytest.raises(OpsMeshError, match="Cron"):
        normalize_schedule(
            PersistentSchedule(id="invalid", principal_id="owner", cron_expression=expression)
        )


def test_impossible_calendar_has_bounded_search():
    with pytest.raises(OpsMeshError, match="five years"):
        normalize_schedule(
            PersistentSchedule(id="invalid", principal_id="owner", cron_expression="0 0 31 2 *")
        )


def test_catch_up_is_bounded_without_discarding_backlog():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    schedule = PersistentSchedule(
        id="backlog",
        principal_id="owner",
        next_run_at=start,
        interval_seconds=10,
        misfire_policy="catch_up",
        max_catch_up=3,
    )
    due, following = due_times(schedule, start + timedelta(seconds=100))
    assert due == [start, start + timedelta(seconds=10), start + timedelta(seconds=20)]
    assert following == start + timedelta(seconds=30)
