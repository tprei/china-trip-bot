"""Flight offers as found by the sources: one direction, one person."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import pairwise
from typing import Literal

from tripbot.domain.money import Money

Direction = Literal["outbound", "return"]


@dataclass(frozen=True)
class Segment:
    """One flight number. Times are local at each airport."""

    origin: str
    destination: str
    depart: datetime
    arrive: datetime
    minutes: int
    airline_code: str
    airline_name: str
    flight_number: str
    logo_url: str | None = None
    stops_on_board: tuple[str, ...] = ()  # same-plane stops, e.g. CA898 via MAD


@dataclass(frozen=True)
class Layover:
    airport: str
    minutes: int


@dataclass(frozen=True)
class Offer:
    """A priced one-way journey for one person."""

    source: str
    group_id: str
    direction: Direction
    segments: tuple[Segment, ...]
    price: Money
    total_minutes: int
    self_transfer: bool = False
    booking_url: str | None = None
    found_on: date | None = field(default=None, compare=False)

    @property
    def origin(self) -> str:
        return self.segments[0].origin

    @property
    def destination(self) -> str:
        return self.segments[-1].destination

    @property
    def depart(self) -> datetime:
        return self.segments[0].depart

    @property
    def arrive(self) -> datetime:
        return self.segments[-1].arrive

    @property
    def stops(self) -> int:
        """Plane changes plus same-plane stops."""
        on_board = sum(len(segment.stops_on_board) for segment in self.segments)
        return len(self.segments) - 1 + on_board

    @property
    def layovers(self) -> list[Layover]:
        pairs = pairwise(self.segments)
        return [
            Layover(
                airport=before.destination,
                minutes=int((after.depart - before.arrive).total_seconds() // 60),
            )
            for before, after in pairs
        ]

    @property
    def via(self) -> tuple[str, ...]:
        airports: list[str] = []
        for index, segment in enumerate(self.segments):
            if index > 0:
                airports.append(segment.origin)
            airports.extend(segment.stops_on_board)
        return tuple(airports)

    @property
    def airlines(self) -> tuple[str, ...]:
        names: list[str] = []
        for segment in self.segments:
            if segment.airline_name not in names:
                names.append(segment.airline_name)
        return tuple(names)

    def summary(self) -> str:
        """`DUB → AMS → CAN · 15h40 · KLM + China Southern`."""
        path = " → ".join((self.origin, *self.via, self.destination))
        return f"{path} · {duration_label(self.total_minutes)} · {' + '.join(self.airlines)}"


def duration_label(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest:02d}" if rest else f"{hours}h"
