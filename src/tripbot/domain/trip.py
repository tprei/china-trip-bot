"""Who travels, from where, and the date windows on the ground in China."""

from __future__ import annotations

from dataclasses import dataclass
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
    name: str
    origin: str
    people: tuple[str, ...]

    @property
    def headcount(self) -> int:
        return len(self.people)


@dataclass(frozen=True)
class Flex:
    outbound_days_before: int
    return_days_after: int


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

    def label(self) -> str:
        """`9 – 20 Aug` or `30 Aug – 10 Sep`."""
        if self.start.month == self.end.month:
            return f"{self.start.day} – {self.end.day} {self.end:%b}"
        return f"{self.start.day} {self.start:%b} – {self.end.day} {self.end:%b}"


def people_count(groups: list[Group]) -> int:
    return sum(group.headcount for group in groups)
