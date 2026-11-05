"""What to search: one direction, one group, one date, between airport sets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tripbot.domain.flights import Direction, Offer


@dataclass(frozen=True)
class SearchKey:
    source: str
    group_id: str
    direction: Direction
    origins: tuple[str, ...]
    destinations: tuple[str, ...]
    travel_date: date

    def label(self) -> str:
        return (
            f"{self.source} {self.group_id} {self.direction} "
            f"{','.join(self.origins)}→{','.join(self.destinations)} {self.travel_date}"
        )


@dataclass(frozen=True)
class SearchResult:
    status: str  # ok | empty | error
    offers: tuple[Offer, ...] = ()
    error: str | None = None
    latency_ms: int | None = None
