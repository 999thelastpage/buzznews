from datetime import datetime, timezone

from buzz_news.rollups import _MONTH_TOP_LIMIT, _ist_month_window


def test_month_top_limit():
    assert _MONTH_TOP_LIMIT == 500


def test_ist_month_window_may_2026():
    start_utc, end_utc, month_str = _ist_month_window(2026, 5)
    assert month_str == "2026-05"
    # 00:00 IST on May 1, 2026 = 18:30 UTC on April 30, 2026
    assert start_utc == datetime(2026, 4, 30, 18, 30, tzinfo=timezone.utc)
    # 00:00 IST on June 1, 2026 = 18:30 UTC on May 31, 2026
    assert end_utc == datetime(2026, 5, 31, 18, 30, tzinfo=timezone.utc)


def test_ist_month_window_december_rolls_to_next_year():
    start_utc, end_utc, month_str = _ist_month_window(2026, 12)
    assert month_str == "2026-12"
    assert start_utc == datetime(2026, 11, 30, 18, 30, tzinfo=timezone.utc)
    # 00:00 IST on Jan 1, 2027 = 18:30 UTC on Dec 31, 2026
    assert end_utc == datetime(2026, 12, 31, 18, 30, tzinfo=timezone.utc)
