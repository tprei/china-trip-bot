"""Who travels, from where, and the date windows on the ground in China."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta


@dataclass(frozen=True)
class Origin:
    code: str
    city: str
    airports: tuple[str, ...]
    currency: str


@dataclass(frozen=True)
class Group:
    """People who always book the same flights."""

    id: str
    origin: str
    people: tuple[str, ...]

    @property
    def name(self) -> str:
        return " + ".join(self.people)

    @property
    def headcount(self) -> int:
        return len(self.people)


@dataclass(frozen=True)
class Flex:
    outbound_days_before: int
    return_days_after: int
    shift_days: int  # the whole trip can also start and end this many days earlier or later
    weekday_return_eur: (
        int  # per person: a return leaving China Mon-Thu ranks as if it cost this much more
    )


@dataclass(frozen=True)
class Window:
    """Days on the ground in China, first to last, both included."""

    id: str
    start: date
    end: date

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    def outbound_dates(self, flex: Flex) -> list[date]:
        """Departure dates to search: up to the first day on the ground."""
        return [
            self.start - timedelta(days=offset)
            for offset in range(flex.outbound_days_before, -1, -1)
        ]

    def return_dates(self, flex: Flex) -> list[date]:
        """Departure dates from China: the last day and the free days after it."""
        return [self.end + timedelta(days=offset) for offset in range(flex.return_days_after + 1)]

    def shifted(self, days: int) -> Window:
        """The same nights, starting and ending `days` later (earlier when negative)."""
        moved = timedelta(days=days)
        return replace(self, start=self.start + moved, end=self.end + moved)

    def variants(self, flex: Flex) -> tuple[Window, ...]:
        """This window slid up to `flex.shift_days` days either way, earliest first."""
        return tuple(self.shifted(days) for days in range(-flex.shift_days, flex.shift_days + 1))

    def label(self) -> str:
        """`9 – 20 Aug` or `30 Aug – 10 Sep`."""
        if self.start.month == self.end.month:
            return f"{self.start.day} – {self.end.day} {self.end:%b}"
        return f"{self.start.day} {self.start:%b} – {self.end.day} {self.end:%b}"


def people_count(groups: list[Group]) -> int:
    return sum(group.headcount for group in groups)
