from datetime import datetime, timezone

from app.heartbeat import seconds_until_next

def test_before_fire_time_waits_until_today():
    now = datetime(2026, 7, 10, 20, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now) == 3600

def test_after_fire_time_waits_until_tomorrow():
    now = datetime(2026, 7, 10, 21, 30, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now) == 23.5 * 3600

def test_exactly_at_fire_time_waits_a_full_day():
    now = datetime(2026, 7, 10, 21, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now) == 24 * 3600
