"""SerpApi's Google Flights API: several airports per side in one search."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from tripbot.domain.flights import Offer, Segment
from tripbot.domain.money import Money
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources.base import SearchShape, timed

URL = "https://serpapi.com/search"
CURRENCY = "EUR"
ONE_WAY = "2"
ONE_STOP_OR_FEWER = "2"
NO_RESULTS = "hasn't returned any results"


class SerpApi:
    name = "serpapi"
    shape = SearchShape(many_airports=True, city_codes=False)

    def __init__(self, client: httpx.Client, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    def search(self, key: SearchKey) -> SearchResult:
        return timed(lambda: self._search(key))

    def _search(self, key: SearchKey) -> SearchResult:
        params = {
            "engine": "google_flights",
            "departure_id": ",".join(key.origins),
            "arrival_id": ",".join(key.destinations),
            "outbound_date": key.travel_date.isoformat(),
            "type": ONE_WAY,
            "stops": ONE_STOP_OR_FEWER,
            "adults": "1",
            "currency": CURRENCY,
            "hl": "en",
            "api_key": self._api_key,
        }
        response = self._client.get(URL, params=params, timeout=90)
        return parse(response.json(), key)


def parse(payload: dict[str, Any], key: SearchKey) -> SearchResult:
    error = payload.get("error")
    if isinstance(error, str):
        if NO_RESULTS in error:
            return SearchResult(status="empty")
        return SearchResult(status="error", error=error[:300])
    items = list(payload.get("best_flights") or []) + list(payload.get("other_flights") or [])
    offers = tuple(to_offer(item, key) for item in items if item.get("price") and item["flights"])
    return SearchResult(status="ok" if offers else "empty", offers=offers)


def to_offer(item: dict[str, Any], key: SearchKey) -> Offer:
    segments = tuple(_segment(flight) for flight in item["flights"])
    return Offer(
        source="serpapi",
        group_id=key.group_id,
        direction=key.direction,
        segments=segments,
        price=Money.of(int(item["price"]), CURRENCY),
        total_minutes=int(item["total_duration"]),
        self_transfer=_self_transfer(item),
    )


def _segment(flight: dict[str, Any]) -> Segment:
    number = str(flight.get("flight_number", ""))
    code = number.split(" ")[0] if number else ""
    return Segment(
        origin=flight["departure_airport"]["id"],
        destination=flight["arrival_airport"]["id"],
        depart=_time(flight["departure_airport"]["time"]),
        arrive=_time(flight["arrival_airport"]["time"]),
        minutes=int(flight["duration"]),
        airline_code=code,
        airline_name=str(flight.get("airline", code)),
        flight_number=number.replace(" ", ""),
        logo_url=flight.get("airline_logo"),
    )


def _time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M")


def _self_transfer(item: dict[str, Any]) -> bool:
    """Google marks separate tickets as "Self transfer" in the extension texts."""
    texts = list(item.get("extensions") or [])
    for flight in item["flights"]:
        texts += list(flight.get("extensions") or [])
    return any("self transfer" in str(text).lower() for text in texts)
