"""Realistic sample flights for tests, reviews and screenshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from tripbot.domain.flights import Offer, Segment
from tripbot.domain.money import Money
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources.base import SearchShape, logo_for

UTC_OFFSET = {"DUB": 1, "LHR": 1, "LGW": 1, "GRU": -3}  # summer; China is +8


@dataclass(frozen=True)
class SampleRoute:
    airline: str
    name: str
    via: tuple[str, ...]
    minutes: int
    eur: int
    same_plane: bool = False


SAMPLE_ROUTES: dict[str, dict[str, SampleRoute]] = {
    "thiago": {
        "PEK": SampleRoute("HU", "Hainan Airlines", (), 640, 330),
        "PVG": SampleRoute("MU", "China Eastern", (), 690, 335),
        "CAN": SampleRoute("KL", "KLM", ("AMS",), 940, 320),
    },
    "lon": {
        "CAN": SampleRoute("CZ", "China Southern", (), 735, 285),
        "PEK": SampleRoute("CA", "Air China", (), 650, 315),
        "PVG": SampleRoute("MU", "China Eastern", (), 700, 305),
    },
    "sao": {
        "PEK": SampleRoute("CA", "Air China", ("MAD",), 1560, 745, same_plane=True),
        "CAN": SampleRoute("EK", "Emirates", ("DXB",), 1630, 700),
        "PVG": SampleRoute("QR", "Qatar Airways", ("DOH",), 1680, 720),
    },
}
HOME = {"thiago": "DUB", "lon": "LHR", "sao": "GRU"}


class SampleSource:
    name = "serpapi"
    shape = SearchShape(many_airports=True, city_codes=False)

    def __init__(self, discount_pct: int = 0) -> None:
        self.discount_pct = discount_pct

    def search(self, key: SearchKey) -> SearchResult:
        offers = tuple(
            sample_offer(key, airport, route, self.discount_pct)
            for airport, route in SAMPLE_ROUTES[key.group_id].items()
        )
        return SearchResult(status="ok", offers=offers)


def sample_offer(key: SearchKey, china: str, route: SampleRoute, discount_pct: int) -> Offer:
    home = HOME[key.group_id]
    outbound = key.direction == "outbound"
    origin, destination = (home, china) if outbound else (china, home)
    depart = datetime.combine(key.travel_date, time(21, 0) if outbound else time(12, 30))
    shift = (8 - UTC_OFFSET[home]) if outbound else (UTC_OFFSET[home] - 8)
    weekend = 1.04 if key.travel_date.weekday() >= 5 else 1.0
    price = round(route.eur * weekend * (100 - discount_pct) / 100)
    segments = _segments(origin, destination, depart, shift, route)
    return Offer(
        source="serpapi",
        group_id=key.group_id,
        direction=key.direction,
        segments=segments,
        price=Money.of(price, "EUR"),
        total_minutes=route.minutes,
    )


def _segments(
    origin: str, destination: str, depart: datetime, shift: int, route: SampleRoute
) -> tuple[Segment, ...]:
    arrive = depart + timedelta(minutes=route.minutes, hours=shift)
    if not route.via or route.same_plane:
        return (
            Segment(
                origin=origin,
                destination=destination,
                depart=depart,
                arrive=arrive,
                minutes=route.minutes,
                airline_code=route.airline,
                airline_name=route.name,
                flight_number=f"{route.airline}898",
                logo_url=logo_for(route.airline),
                stops_on_board=route.via,
            ),
        )
    first_leg = route.minutes * 2 // 5
    stop = route.via[0]
    land = depart + timedelta(minutes=first_leg, hours=shift // 2)
    leave = land + timedelta(hours=3)
    return (
        Segment(
            origin,
            stop,
            depart,
            land,
            first_leg,
            route.airline,
            route.name,
            f"{route.airline}1",
            logo_for(route.airline),
        ),
        Segment(
            stop,
            destination,
            leave,
            arrive,
            route.minutes - first_leg - 180,
            route.airline,
            route.name,
            f"{route.airline}2",
            logo_for(route.airline),
        ),
    )
