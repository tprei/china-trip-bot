"""Small builders for offers used across tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

from tripbot.domain.flights import Direction, Offer, Segment
from tripbot.domain.money import Money


def offer(
    group_id: str,
    direction: Direction,
    path: list[str],
    depart: datetime,
    price: Money,
    *,
    hours_per_hop: int = 6,
    layover_hours: int = 2,
    stops_on_board: tuple[str, ...] = (),
    self_transfer: bool = False,
    source: str = "test",
) -> Offer:
    """An offer flying `path` (airports in order), one segment per hop."""
    segments = []
    clock = depart
    for index, (origin, destination) in enumerate(pairwise(path)):
        arrive = clock + timedelta(hours=hours_per_hop)
        segments.append(
            Segment(
                origin=origin,
                destination=destination,
                depart=clock,
                arrive=arrive,
                minutes=hours_per_hop * 60,
                airline_code="XX",
                airline_name=f"Airline {index + 1}",
                flight_number=f"XX{100 + index}",
                stops_on_board=stops_on_board if index == 0 else (),
            )
        )
        clock = arrive + timedelta(hours=layover_hours)
    total = int((segments[-1].arrive - segments[0].depart).total_seconds() // 60)
    return Offer(
        source=source,
        group_id=group_id,
        direction=direction,
        segments=tuple(segments),
        price=price,
        total_minutes=total,
        self_transfer=self_transfer,
    )


def eur(amount: int) -> Money:
    return Money.of(amount, "EUR")
