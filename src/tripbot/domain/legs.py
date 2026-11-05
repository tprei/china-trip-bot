"""Ways to move between two cities inside China."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from tripbot.domain.money import Money

Mode = Literal["train", "sleeper", "flight"]

MORNING_START_MINUTES = 8 * 60
SLEEPER_WAKEUP_MINUTES = 30


@dataclass(frozen=True)
class LegOption:
    mode: Mode
    name: str
    minutes: int
    fare: Money
    overhead_minutes: int = 0
    depart: str | None = None
    arrive: str | None = None
    runs_on: frozenset[int] | None = None  # weekdays, Monday is 0; None means daily
    cabin: str | None = None
    estimate: bool = False

    @property
    def overnight(self) -> bool:
        return self.mode == "sleeper"

    def runs(self, day: date) -> bool:
        return self.runs_on is None or day.weekday() in self.runs_on

    def daytime_minutes(self) -> int:
        """Waking time spent travelling. A sleeper only costs the morning after 08:00."""
        if not self.overnight:
            return self.minutes + self.overhead_minutes
        if self.arrive is None:
            return SLEEPER_WAKEUP_MINUTES
        hours, minutes = (int(part) for part in self.arrive.split(":"))
        late = hours * 60 + minutes - MORNING_START_MINUTES
        return max(0, late) + SLEEPER_WAKEUP_MINUTES


@dataclass(frozen=True)
class LegTable:
    options: Mapping[tuple[str, str], tuple[LegOption, ...]]
    fallback_flight: LegOption

    def between(self, origin: str, destination: str) -> tuple[LegOption, ...]:
        """Listed options, or a generic domestic flight when nothing is listed."""
        listed = self.options.get((origin, destination), ())
        return listed if listed else (self.fallback_flight,)

    def listed(self) -> list[tuple[str, str, LegOption]]:
        return [
            (origin, destination, option)
            for (origin, destination), options in self.options.items()
            for option in options
        ]
