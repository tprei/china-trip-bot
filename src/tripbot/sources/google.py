"""Google Flights through the unofficial `faster-flights` scraper.

One origin and one destination airport per search, one-way only (its round
trips are unreliable). It runs from the home connection; if Google starts
blocking it, searches come back as errors and the bot says so.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

from fast_flights import FlightQuery, Passengers, create_query, get_flights
from fast_flights.model import Flights, SimpleDatetime, SingleFlight

from tripbot.domain.flights import Offer, Segment
from tripbot.domain.money import Money
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources.base import SearchShape, logo_for, timed

CURRENCY = "EUR"


class GoogleFlights:
    name = "google"
    shape = SearchShape(many_airports=False, city_codes=False)

    def search(self, key: SearchKey) -> SearchResult:
        return timed(lambda: self._search(key))

    def _search(self, key: SearchKey) -> SearchResult:
        query = create_query(
            flights=[
                FlightQuery(
                    date=key.travel_date.isoformat(),
                    from_airport=key.origins[0],
                    to_airport=key.destinations[0],
                )
            ],
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
            currency=CURRENCY,
            max_stops=1,
        )
        results = get_flights(query)
        diagnostics = results.diagnostics
        if diagnostics is not None and diagnostics.status in ("blocked", "http_error"):
            return SearchResult(status="error", error=f"google {diagnostics.status}")
        names = {airline.code: airline.name for airline in results.metadata.airlines}
        offers = tuple(to_offer(flight, key, names) for flight in results if flight.flights)
        return SearchResult(status="ok" if offers else "empty", offers=offers)


def to_offer(flight: Flights, key: SearchKey, airline_names: dict[str, str]) -> Offer:
    segments = tuple(_segment(single, airline_names, flight.airlines) for single in flight.flights)
    layover_minutes = sum(
        int((after.depart - before.arrive).total_seconds() // 60)
        for before, after in pairwise(segments)
    )
    return Offer(
        source="google",
        group_id=key.group_id,
        direction=key.direction,
        segments=segments,
        price=Money.of(flight.price, CURRENCY),
        total_minutes=sum(s.minutes for s in segments) + layover_minutes,
    )


def _segment(single: SingleFlight, names: dict[str, str], fallback: list[str]) -> Segment:
    code = single.airline_code
    name = names.get(code) or (fallback[0] if fallback else code)
    return Segment(
        origin=single.from_airport.code,
        destination=single.to_airport.code,
        depart=_datetime(single.departure),
        arrive=_datetime(single.arrival),
        minutes=single.duration,
        airline_code=code,
        airline_name=name,
        flight_number=f"{code}{single.flight_number}",
        logo_url=logo_for(code),
    )


def _datetime(value: SimpleDatetime) -> datetime:
    year, month, day = value.date
    hour, minute = value.time if value.time else (0, 0)
    return datetime(year, month, day) + timedelta(hours=hour, minutes=minute)
