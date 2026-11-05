"""Trip.com flight search through the JSON endpoint its own pages call.

Searches by city code (LON, BJS, SHA, SAO) and sees Chinese carriers Google
misses. The endpoint is undocumented and disallowed in robots.txt; we use it
for a handful of personal searches a night, 4 s apart. The request body must
stay exactly as written: HTTP 432 means `head` lost a field, HTTP 400 means
`extension` lost sotpLocale or sotpCurrency.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import httpx

from tripbot.domain.flights import Offer, Segment
from tripbot.domain.money import Money
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources.base import SearchShape, logo_for, timed

URL = "https://www.trip.com/restapi/soa2/27015/FlightListSearchSSE"
CURRENCY = "USD"
SPACING_SECONDS = 4.0
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Origin": "https://www.trip.com",
    "Referer": "https://www.trip.com/flights/",
}
# Carriers whose tickets never connect to another airline's: a mix means separate tickets.
LOW_COST = {"FR", "U2", "W6", "W4", "VY", "LS", "HV", "DY", "D8", "PC", "AK", "D7", "TR"}


class TripCom:
    name = "tripcom"
    shape = SearchShape(many_airports=False, city_codes=True)

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._last_call = 0.0

    def search(self, key: SearchKey) -> SearchResult:
        return timed(lambda: self._search(key))

    def _search(self, key: SearchKey) -> SearchResult:
        wait = self._last_call + SPACING_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        body = request_body(key.origins[0], key.destinations[0], key.travel_date.isoformat())
        response = self._client.post(URL, content=json.dumps(body), headers=HEADERS, timeout=60)
        if response.status_code != 200:
            return SearchResult(status="error", error=f"HTTP {response.status_code}")
        return parse(last_frame(response.text), key)


def request_body(depart: str, arrive: str, date: str) -> dict[str, Any]:
    return {
        "mode": 0,
        "searchCriteria": {
            "grade": 1,
            "realGrade": 1,
            "tripType": 1,
            "journeyNo": 1,
            "passengerInfoType": {"adultCount": 1, "childCount": 0, "infantCount": 0},
            "journeyInfoTypes": [
                {
                    "journeyNo": 1,
                    "departDate": date,
                    "departCode": depart,
                    "arriveCode": arrive,
                    "departAirport": "",
                    "arriveAirport": "",
                }
            ],
            "policyId": None,
        },
        "sortInfoType": {"direction": True, "orderBy": "Price", "topList": []},
        "tagList": [],
        "flagList": ["NEED_RESET_SORT"],
        "filterType": {
            "filterFlagTypes": [],
            "queryItemSettings": [],
            "studentsSelectedStatus": True,
        },
        "abtList": [],
        "head": {
            "cid": "",
            "ctok": "",
            "cver": "3",
            "lang": "01",
            "sid": "8888",
            "syscode": "40",
            "auth": "",
            "xsid": "",
            "extension": [
                {"name": "source", "value": "ONLINE"},
                {"name": "sotpGroup", "value": "Trip"},
                {"name": "sotpLocale", "value": "en-US"},
                {"name": "sotpCurrency", "value": CURRENCY},
                {"name": "useDistributionType", "value": "1"},
            ],
            "Locale": "en-US",
            "Language": "en",
            "Currency": CURRENCY,
            "ClientID": "",
            "appid": "700020",
        },
    }


def last_frame(stream: str) -> dict[str, Any]:
    """The server streams `data:` frames; the last one holds the full list."""
    frames = [frame for frame in stream.split("data:") if frame.strip()]
    if not frames:
        raise ValueError("Trip.com returned no data frames")
    payload = json.loads(frames[-1].strip().splitlines()[0])
    if not isinstance(payload, dict):
        raise ValueError("Trip.com frame is not an object")
    return payload


def parse(payload: dict[str, Any], key: SearchKey) -> SearchResult:
    airlines = {a["code"]: a["name"] for a in payload.get("airlineList") or []}
    itineraries = payload.get("itineraryList") or []
    offers = tuple(to_offer(itinerary, key, airlines) for itinerary in itineraries)
    return SearchResult(status="ok" if offers else "empty", offers=offers)


def to_offer(itinerary: dict[str, Any], key: SearchKey, airlines: dict[str, str]) -> Offer:
    journey = itinerary["journeyList"][0]
    segments = tuple(_segment(leg, airlines) for leg in journey["transSectionList"])
    price = itinerary["policies"][0]["price"]["totalPrice"]
    return Offer(
        source="tripcom",
        group_id=key.group_id,
        direction=key.direction,
        segments=segments,
        price=Money.of(str(price), CURRENCY),
        total_minutes=int(journey["duration"]),
        self_transfer=_separate_tickets(segments),
    )


def _segment(leg: dict[str, Any], airlines: dict[str, str]) -> Segment:
    info = leg["flightInfo"]
    code = str(info["airlineCode"])
    depart = _time(leg["departDateTime"])
    arrive = _time(leg["arriveDateTime"])
    return Segment(
        origin=leg["departPoint"]["airportCode"],
        destination=leg["arrivePoint"]["airportCode"],
        depart=depart,
        arrive=arrive,
        minutes=int(leg.get("duration") or 0),
        airline_code=code,
        airline_name=airlines.get(code, code),
        flight_number=str(info["flightNo"]),
        logo_url=logo_for(code),
        stops_on_board=tuple(stop["airportCode"] for stop in info.get("stopList") or []),
    )


def _time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _separate_tickets(segments: tuple[Segment, ...]) -> bool:
    carriers = {segment.airline_code for segment in segments}
    return len(carriers) > 1 and bool(carriers & LOW_COST)
