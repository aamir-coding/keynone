"""The settings window's Home page: status, shortcuts and statistics."""

from __future__ import annotations

from keynone.settings.home_view import duration, speaking_speed, time_saved


def test_time_saved_and_formats() -> None:
    assert time_saved(400, 60) == 540  # 10 min of typing at 40 wpm, 1 min of speaking
    assert time_saved(10, 600) == 0
    assert duration(20) == "under a minute"
    assert duration(12 * 60) == "12 min"
    assert duration(135 * 60) == "2 h 15 min"
    assert duration(180 * 60) == "3 h"
    assert speaking_speed(300, 120) == 150
    assert speaking_speed(5, 3) == 0  # too little speech to tell
