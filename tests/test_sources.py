import json
from datetime import date, datetime
from pathlib import Path

import httpx
from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight

from tripbot.domain.money import Money
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources import google, serpapi, tripcom
from tripbot.sources.base import timed

FIXTURES = Path(__file__).parent / "fixtures"


def key(source: str, origins: tuple[str, ...], destinations: tuple[str, ...]) -> SearchKey:
    return SearchKey(
        source=source,
        group_id="lon",
        direction="outbound",
        origins=origins,
        destinations=destinations,
        travel_date=date(2027, 8, 28),
    )


def single(
    origin: str,
    destination: str,
    depart: tuple[int, int],
    arrive: tuple[int, int],
    day: int,
    arrive_day: int,
    minutes: int,
    code: str,
    number: str,
) -> SingleFlight:
    return SingleFlight(
        from_airport=Airport(name=origin, code=origin),
        to_airport=Airport(name=destination, code=destination),
        departure=SimpleDatetime(date=(2027, 8, day), time=depart),
        arrival=SimpleDatetime(date=(2027, 8, arrive_day), time=arrive),
        duration=minutes,
        plane_type="A350",
        airline_code=code,
        flight_number=number,
    )


def test_google_offer_keeps_segments_and_adds_layovers() -> None:
    flight = Flights(
        type="one-way",
        price=612,
        airlines=["Qatar Airways"],
        flights=[
            single("LHR", "DOH", (8, 0), (17, 0), 28, 28, 420, "QR", "8"),
            single("DOH", "PEK", (20, 0), (9, 0), 28, 29, 480, "QR", "306"),
        ],
        carbon=CarbonEmission(typical_on_route=0, emission=0),
    )
    found = google.to_offer(flight, key("google", ("LHR",), ("PEK",)), {"QR": "Qatar Airways"})
    assert found.price == Money(61200, "EUR")
    assert found.via == ("DOH",)
    assert found.total_minutes == 420 + 180 + 480
    assert found.segments[1].flight_number == "QR306"
    assert found.segments[0].logo_url == "https://www.gstatic.com/flights/airline_logos/70px/QR.png"


def test_serpapi_parses_best_and_other_flights() -> None:
    payload = json.loads((FIXTURES / "serpapi-lon-china.json").read_text(encoding="utf-8"))
    result = serpapi.parse(payload, key("serpapi", ("LHR", "LGW"), ("PEK", "CAN", "PVG")))
    assert result.status == "ok"
    assert [o.destination for o in result.offers] == ["CAN", "PEK", "PVG"]
    direct = result.offers[0]
    assert direct.price == Money(59800, "EUR")
    assert direct.segments[0].depart == datetime(2027, 8, 28, 21, 0)
    assert direct.segments[0].flight_number == "CZ304"
    assert direct.segments[0].logo_url is not None
    assert result.offers[2].self_transfer
    assert not direct.self_transfer


def test_serpapi_no_results_is_empty_not_an_error() -> None:
    payload = {"error": "Google Flights hasn't returned any results for this query."}
    assert serpapi.parse(payload, key("serpapi", ("DUB",), ("CSX",))).status == "empty"
    assert serpapi.parse(
        {"error": "Invalid API key."}, key("serpapi", ("DUB",), ("CSX",))
    ).status == ("error")


def test_tripcom_reads_the_last_frame() -> None:
    stream = (FIXTURES / "tripcom-lon-bjs.sse").read_text(encoding="utf-8")
    result = tripcom.parse(tripcom.last_frame(stream), key("tripcom", ("LON",), ("BJS",)))
    assert len(result.offers) == 3
    nonstop, jordan, separate = result.offers
    assert nonstop.price == Money(97600, "USD") and nonstop.stops == 0
    assert nonstop.segments[0].airline_name == "Air China"
    assert jordan.via == ("AMM",)
    assert separate.self_transfer and separate.price == Money(45550, "USD")


def test_tripcom_request_keeps_the_head_contract() -> None:
    body = tripcom.request_body("LON", "BJS", "2027-08-20")
    head = body["head"]
    assert (head["appid"], head["Locale"], head["Language"], head["Currency"]) == (
        "700020",
        "en-US",
        "en",
        "USD",
    )
    names = {item["name"]: item["value"] for item in head["extension"]}
    assert names["sotpLocale"] == "en-US" and names["sotpCurrency"] == "USD"


def test_tripcom_http_errors_become_failed_searches() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(432)

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        source = tripcom.TripCom(client)
        result = source.search(key("tripcom", ("LON",), ("BJS",)))
    assert result.status == "error" and result.error == "HTTP 432"
    assert result.latency_ms is not None


def test_a_crashing_source_is_recorded_not_raised() -> None:
    def explode() -> SearchResult:
        raise RuntimeError("Google Flights returned HTTP 429.")

    result = timed(explode)
    assert result.status == "error"
    assert result.error is not None and "HTTP 429" in result.error
