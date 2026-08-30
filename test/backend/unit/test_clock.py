from __future__ import annotations

import pytest
from core.backend.app.domain.clock import WorldClock
from core.backend.app.domain.errors import (
    ChapterAlreadyEndedError,
    InvalidTimeAdvanceError,
)


def test_clock_crosses_overnight_window() -> None:
    clock = WorldClock.day_one_start()
    assert clock.advance(540).label == "Day1 18:00"
    assert clock.advance(1).label == "Day2 08:01"


def test_paused_clock_rejects_advance_and_can_resume() -> None:
    clock = WorldClock.day_one_start()
    clock.pause()
    with pytest.raises(InvalidTimeAdvanceError):
        clock.advance(1)
    clock.resume()
    assert clock.advance(1).label == "Day1 09:01"


def test_day_seven_end_and_after_end_rejection() -> None:
    clock = WorldClock.day_one_start()
    # 6 complete active days after Day1 09:00, then the final 9 hours.
    assert clock.advance(6 * 600 + 540).label == "Day7 18:00"
    assert clock.is_ended
    with pytest.raises(ChapterAlreadyEndedError):
        clock.advance(1)


def test_overshoot_is_rejected_without_partial_mutation() -> None:
    clock = WorldClock.day_one_start()
    with pytest.raises(InvalidTimeAdvanceError):
        clock.advance(6 * 600 + 541)
    assert clock.current.label == "Day1 09:00"


@pytest.mark.parametrize("minutes", [0, -1, True])
def test_illegal_advance_values(minutes: object) -> None:
    clock = WorldClock.day_one_start()
    with pytest.raises(InvalidTimeAdvanceError):
        clock.advance(minutes)  # type: ignore[arg-type]

