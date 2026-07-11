from datetime import datetime, timedelta, timezone

from app.scheduler import (
    STALE_MARKET_SECONDS,
    STALE_OFF_SECONDS,
    is_market_hours,
    is_stale,
    next_sync_delay,
)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- is_market_hours: 13:30–20:00 UTC, Mon–Fri ---

def test_market_hours_weekday_midday():
    assert is_market_hours(utc(2026, 7, 8, 15, 0)) is True  # Wednesday

def test_market_hours_open_boundary_inclusive():
    assert is_market_hours(utc(2026, 7, 8, 13, 30)) is True

def test_market_hours_just_before_open():
    assert is_market_hours(utc(2026, 7, 8, 13, 29, 59)) is False

def test_market_hours_close_boundary_exclusive():
    assert is_market_hours(utc(2026, 7, 8, 20, 0)) is False

def test_market_hours_just_before_close():
    assert is_market_hours(utc(2026, 7, 8, 19, 59, 59)) is True

def test_market_hours_weekend_false():
    assert is_market_hours(utc(2026, 7, 11, 15, 0)) is False  # Saturday
    assert is_market_hours(utc(2026, 7, 12, 15, 0)) is False  # Sunday

def test_market_hours_weekday_off_hours():
    assert is_market_hours(utc(2026, 7, 8, 2, 0)) is False
    assert is_market_hours(utc(2026, 7, 8, 22, 0)) is False


# --- next_sync_delay: 900s during market hours, 3600s otherwise ---

def test_delay_market_hours():
    assert next_sync_delay(utc(2026, 7, 8, 15, 0)) == 900

def test_delay_off_hours():
    assert next_sync_delay(utc(2026, 7, 8, 22, 0)) == 3600

def test_delay_weekend():
    assert next_sync_delay(utc(2026, 7, 11, 15, 0)) == 3600


# --- staleness constants + is_stale ---

def test_stale_constants():
    assert STALE_MARKET_SECONDS == 45 * 60
    assert STALE_OFF_SECONDS == 2 * 3600

def test_stale_none_last_synced():
    assert is_stale(None, utc(2026, 7, 8, 15, 0)) is True

def test_stale_market_hours_threshold():
    now = utc(2026, 7, 8, 15, 0)
    assert is_stale(now - timedelta(minutes=44), now) is False
    assert is_stale(now - timedelta(minutes=46), now) is True

def test_stale_off_hours_threshold():
    now = utc(2026, 7, 8, 22, 0)
    assert is_stale(now - timedelta(minutes=46), now) is False  # would be stale in-market
    assert is_stale(now - timedelta(hours=2, minutes=1), now) is True
