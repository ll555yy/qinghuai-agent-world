"""Pure virtual-world clock rules for the first backend phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .errors import ChapterAlreadyEndedError, InvalidTimeAdvanceError

ClockStatus = Literal["running", "paused", "chapter_ended"]


@dataclass(frozen=True, slots=True)
class WorldTime:
    """A validated world time value."""

    day: int
    hour: int
    minute: int

    def __post_init__(self) -> None:
        if not 1 <= self.day <= 7:
            raise ValueError("day must be between 1 and 7")
        if not 0 <= self.hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        if not 0 <= self.minute <= 59:
            raise ValueError("minute must be between 0 and 59")

    @property
    def clock_minutes(self) -> int:
        return self.hour * 60 + self.minute

    @property
    def label(self) -> str:
        return f"Day{self.day} {self.hour:02d}:{self.minute:02d}"

    def as_dict(self) -> dict[str, int | str]:
        return {
            "day": self.day,
            "hour": self.hour,
            "minute": self.minute,
            "time": f"{self.hour:02d}:{self.minute:02d}",
            "label": self.label,
        }


@dataclass(slots=True)
class WorldClock:
    """Mutable clock whose transitions are limited to the active day window.

    ``advance`` consumes only active-window minutes.  Crossing 18:00 jumps to
    the following day's 08:00 before consuming more minutes.  The operation is
    calculated on local variables first, so a request that would overshoot the
    chapter boundary cannot leave a partially advanced clock behind.
    """

    current: WorldTime = WorldTime(day=1, hour=9, minute=0)
    status: ClockStatus = "running"
    active_start_minutes: int = 8 * 60
    active_end_minutes: int = 18 * 60
    final_day: int = 7

    def __post_init__(self) -> None:
        if self.active_start_minutes >= self.active_end_minutes:
            raise ValueError("active window must have a positive duration")
        if self.final_day < 1 or self.final_day > 7:
            raise ValueError("final_day must be between 1 and 7")
        if not self.active_start_minutes <= self.current.clock_minutes <= self.active_end_minutes:
            raise ValueError("current time must be within the active window")
        if self.current.day > self.final_day:
            raise ValueError("current day cannot be after final_day")

    @classmethod
    def day_one_start(
        cls,
        *,
        start_hour: int = 9,
        start_minute: int = 0,
        active_start_minutes: int = 8 * 60,
        active_end_minutes: int = 18 * 60,
        final_day: int = 7,
    ) -> WorldClock:
        return cls(
            current=WorldTime(day=1, hour=start_hour, minute=start_minute),
            active_start_minutes=active_start_minutes,
            active_end_minutes=active_end_minutes,
            final_day=final_day,
        )

    def clone(self) -> WorldClock:
        return WorldClock(
            current=self.current,
            status=self.status,
            active_start_minutes=self.active_start_minutes,
            active_end_minutes=self.active_end_minutes,
            final_day=self.final_day,
        )

    @property
    def is_ended(self) -> bool:
        return self.status == "chapter_ended"

    def pause(self) -> None:
        if self.status == "chapter_ended":
            raise ChapterAlreadyEndedError()
        self.status = "paused"

    def resume(self) -> None:
        if self.status == "chapter_ended":
            raise ChapterAlreadyEndedError()
        self.status = "running"

    def advance(self, virtual_minutes: int) -> WorldTime:
        """Advance by active virtual minutes and return the new time."""

        if isinstance(virtual_minutes, bool) or not isinstance(virtual_minutes, int):
            raise InvalidTimeAdvanceError(
                "virtualMinutes must be a positive integer.",
                details={"virtualMinutes": virtual_minutes},
            )
        if virtual_minutes <= 0:
            raise InvalidTimeAdvanceError(
                "virtualMinutes must be greater than zero.",
                details={"virtualMinutes": virtual_minutes},
            )
        if self.status == "chapter_ended":
            raise ChapterAlreadyEndedError()
        if self.status == "paused":
            raise InvalidTimeAdvanceError("The world clock is paused.")

        day = self.current.day
        clock_minutes = self.current.clock_minutes
        remaining = virtual_minutes

        while remaining:
            if clock_minutes > self.active_end_minutes:
                raise InvalidTimeAdvanceError("Current time is outside the active window.")
            if clock_minutes == self.active_end_minutes:
                if day == self.final_day:
                    raise InvalidTimeAdvanceError(
                        "The requested advance would pass the chapter end.",
                        details={"chapterEnd": f"Day{self.final_day} 18:00"},
                    )
                day += 1
                clock_minutes = self.active_start_minutes
                continue

            available = self.active_end_minutes - clock_minutes
            consumed = min(remaining, available)
            clock_minutes += consumed
            remaining -= consumed

            if remaining and clock_minutes == self.active_end_minutes:
                if day == self.final_day:
                    raise InvalidTimeAdvanceError(
                        "The requested advance would pass the chapter end.",
                        details={"chapterEnd": f"Day{self.final_day} 18:00"},
                    )
                day += 1
                clock_minutes = self.active_start_minutes

        new_time = WorldTime(day=day, hour=clock_minutes // 60, minute=clock_minutes % 60)
        self.current = new_time
        if day == self.final_day and clock_minutes == self.active_end_minutes:
            self.status = "chapter_ended"
        return new_time

    def as_dict(self) -> dict[str, int | str]:
        result = self.current.as_dict()
        result["status"] = self.status
        return result

