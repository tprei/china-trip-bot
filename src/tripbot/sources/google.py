"""Google Flights through the unofficial `faster-flights` scraper.

One origin and one destination airport per search, one-way only (its round
trips are unreliable). It runs from the home connection; if Google starts
blocking it, searches come back as errors and the bot says so.
"""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.model import Flights, SimpleDatetime, SingleFlight

from tripbot.domain.flights import Offer, Segment
from tripbot.domain.money import Money
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources.base import SearchShape, logo_for, timed
from tripbot.sources.google_payload import get_flights

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
        offers: list[Offer] = []
        rejected: list[str] = []
        for index, flight in enumerate(results):
            try:
                offers.append(to_offer(flight, key, names))
            except (ValueError, TypeError, OverflowError, AttributeError):
                # Upstream dataclasses do not validate JSON. Never log tokens.
                rejected.append(str(index))
        error = None
        if rejected:
            error = (
                f"google rejected {len(rejected)}/{len(results)} invalid itineraries"
                f" (indices {','.join(rejected[:10])})"
            )
        status = "ok" if offers else ("error" if rejected else "empty")
        return SearchResult(status=status, offers=tuple(offers), error=error)


def to_offer(flight: Flights, key: SearchKey, airline_names: dict[str, str]) -> Offer:
    segments = tuple(_segment(single, airline_names, flight.airlines) for single in flight.flights)
    if not segments or type(flight.price) is not int or flight.price <= 0:
        raise ValueError("missing segments or invalid price")
    if (
        segments[0].origin not in key.origins
        or segments[-1].destination not in key.destinations
        or segments[0].depart.date() != key.travel_date
    ):
        raise ValueError("itinerary does not match search")
    layover_minutes = sum(_layover(before, after) for before, after in pairwise(segments))
    return Offer(
        source="google",
        group_id=key.group_id,
        direction=key.direction,
        segments=segments,
        price=Money.of(flight.price, CURRENCY),
        total_minutes=sum(s.minutes for s in segments) + layover_minutes,
    )


def search_url(offer: Offer, language: str, currency: str) -> str:
    """Google Flights on the offer's day and airports, narrowed to its airlines and stops."""
    airlines = list(dict.fromkeys(segment.airline_code for segment in offer.segments))
    query = create_query(
        flights=[
            FlightQuery(
                date=offer.depart.date().isoformat(),
                from_airport=offer.origin,
                to_airport=offer.destination,
                max_stops=offer.stops,
                airlines=airlines,
            )
        ],
        trip="one-way",
        passengers=Passengers(adults=1),
        language=language,
        currency=currency,
        max_stops=offer.stops,
    )
    return query.url()


def _segment(single: SingleFlight, names: dict[str, str], fallback: list[str]) -> Segment:
    if type(single.duration) is not int or single.duration <= 0:
        raise ValueError("invalid segment duration")
    if not single.from_airport.code or not single.to_airport.code:
        raise ValueError("missing airport")
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


def _layover(before: Segment, after: Segment) -> int:
    # Subtract local clocks only at the SAME airport. Segment elapsed time
    # comes from the provider, never from naive cross-timezone subtraction.
    if before.destination != after.origin:
        raise ValueError("airport transfer has unknown elapsed time")
    minutes = int((after.depart - before.arrive).total_seconds() // 60)
    if minutes < 0:
        raise ValueError("negative layover")
    return minutes


def _datetime(value: SimpleDatetime) -> datetime:
    # 3.8.0 passes JSON through unchanged despite int tuple annotations.
    # Never default midnight, infer year/day rollover, or guess a timezone.
    year, month, day = _integers(value.date, 3)
    hour, minute = _time(value.time)
    return datetime(year, month, day, hour, minute)


def _time(value: object) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 1:
        (hour,) = _integers(value, 1)
        return hour, 0
    if isinstance(value, (list, tuple)) and len(value) == 2 and value[0] is None:
        (minute,) = _integers(value[1:], 1)
        return 0, minute
    hour, minute = _integers(value, 2)
    return hour, minute


def _integers(value: object, length: int) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("missing date/time components")
    parts: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise ValueError("invalid date/time component")
        parts.append(item)
    return parts
