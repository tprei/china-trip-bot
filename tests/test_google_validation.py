"""Synthetic wire-shaped regression: the pilot did not retain the failing record."""

import json
from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pytest
from fast_flights.parser import parse_payload
from test_sources import key

from tripbot.domain.searches import SearchResult
from tripbot.sources import google


def entry() -> list[Any]:
    segment: list[Any] = [None] * 23
    segment[3:7] = ["LHR", "Heathrow", "Beijing", "PEK"]
    segment[8], segment[10], segment[11] = [17, 0], [10, 0], 600
    segment[17] = "A350"
    segment[20:23] = [[2027, 8, 28], [2027, 8, 29], ["CA", "938"]]
    flight: list[Any] = [None] * 23
    flight[:3] = ["one-way", ["Air China"], [segment]]
    flight[22] = [0] * 9
    return [flight, [[None, 616]]]


def search(monkeypatch: pytest.MonkeyPatch, entries: list[list[Any]]) -> SearchResult:
    payload: list[Any] = [None] * 4
    payload[3] = [entries]
    # JSON round trip reproduces lists/nulls passed unchanged by 3.8.0.
    results = parse_payload(json.loads(json.dumps(payload)))
    monkeypatch.setattr(google, "get_flights", lambda _: results)
    return google.GoogleFlights().search(key("google", ("LHR",), ("PEK",)))


@pytest.mark.parametrize(
    "bad_time",
    [None, [], [None], [None, None], [0, None], [24, 0], [12, 60], [24], [None, 60]],
)
def test_bad_time_does_not_destroy_valid_fare(
    monkeypatch: pytest.MonkeyPatch, bad_time: object
) -> None:
    bad = entry()
    bad[0][2][0][8] = bad_time
    result = search(monkeypatch, [bad, entry()])
    assert result.status == "ok" and len(result.offers) == 1
    assert result.error == "google rejected 1/2 invalid itineraries (indices 0)"
    assert result.offers[0].price.minor == 61600
    assert result.offers[0].segments[0].arrive == datetime(2027, 8, 29, 10)
    assert result.offers[0].total_minutes == 600  # not naive cross-zone subtraction


@pytest.mark.parametrize("bad_date", [None, [], [None, 8, 28], [2027, 2, 30], [2026, 8, 28]])
def test_all_invalid_is_error_not_no_flights(
    monkeypatch: pytest.MonkeyPatch, bad_date: object
) -> None:
    bad = entry()
    bad[0][2][0][20] = bad_date
    result = search(monkeypatch, [bad])
    assert result.status == "error" and not result.offers
    assert result.error is not None


def test_true_empty_and_valid_results(monkeypatch: pytest.MonkeyPatch) -> None:
    assert search(monkeypatch, []).status == "empty"
    result = search(monkeypatch, [entry(), entry()])
    assert result.status == "ok" and result.error is None
    assert len(result.offers) == 1  # upstream deduplication remains intact


@pytest.mark.parametrize(
    ("wire", "expected"), [([12], (12, 0)), ([None, 50], (0, 50)), ([5, 10], (5, 10))]
)
def test_google_elided_time_parts_are_zero(wire: list[Any], expected: tuple[int, int]) -> None:
    item = entry()
    item[0][2][0][8] = wire
    flight = parse_payload(json.loads(json.dumps([None, None, None, [[item]]])))[0]
    found = google.to_offer(flight, key("google", ("LHR",), ("PEK",)), {})
    assert found.segments[0].depart == datetime(2027, 8, 28, *expected)


def test_explicit_year_boundary_and_midnight() -> None:
    item = entry()
    item[0][2][0][20:22] = [[2027, 12, 31], [2028, 1, 1]]
    item[0][2][0][10] = [0, 0]
    payload: list[Any] = [None, None, None, [[item]]]
    flight = parse_payload(payload)[0]
    query = replace(key("google", ("LHR",), ("PEK",)), travel_date=date(2027, 12, 31))
    found = google.to_offer(flight, query, {})
    assert found.segments[0].arrive == datetime(2028, 1, 1)
    assert found.segments[0].arrive.tzinfo is None


@pytest.mark.parametrize(("transfer", "departure"), [("DOH", [12, 0]), ("PEK", [9, 0])])
def test_unknown_transfer_or_negative_layover_is_rejected(
    transfer: str, departure: list[int]
) -> None:
    item = entry()
    onward = entry()[0][2][0]
    onward[3], onward[6] = transfer, "CAN"
    onward[20], onward[8] = [2027, 8, 29], departure
    item[0][2].append(onward)
    flight = parse_payload([None, None, None, [[item]]])[0]
    with pytest.raises(ValueError, match=r"airport transfer|negative layover"):
        google.to_offer(flight, key("google", ("LHR",), ("CAN",)), {})
