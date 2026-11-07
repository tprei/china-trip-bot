"""Anonymized wire-shape data, with synthetic envelopes for format edge cases."""

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
from fast_flights.parser import parse_js
from fast_flights.querying import Query
from test_sources import key

from tripbot.sources import google, google_payload

PAYLOAD = (Path(__file__).parent / "fixtures/google-lhr-pek-sanitized.json").read_text()


def callback(payload: str = PAYLOAD, side: str = "{}") -> str:
    return (
        "AF_initDataCallback({key: 'ds:1', hash: 'redacted', data:"
        + payload
        + (", sideChannel: " + side + "});")
    )


def test_anonymized_payload_survives_full_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fetch(query: Query) -> str:
        calls.append(query)
        return "<script>" + callback() + "</script>"

    monkeypatch.setattr(google_payload, "fetch_flights_html", fetch)
    query = replace(key("google", ("LHR",), ("PEK",)), travel_date=date(2030, 3, 1))
    result = google.GoogleFlights().search(query)
    assert len(calls) == 1
    assert calls[0].currency == "EUR"
    assert result.status == "ok"
    assert len(result.offers) == 11
    assert result.error is None
    amman, cathay, emirates = result.offers[0], result.offers[1], result.offers[4]
    assert amman.segments[0].arrive == datetime(2030, 3, 2, 0, 10)
    assert cathay.price.minor == 77900
    assert cathay.segments[0].depart == datetime(2030, 3, 1, 17, 0)
    assert emirates.price.minor == 100000
    assert emirates.segments[0].depart == datetime(2030, 3, 1, 16, 50)
    assert emirates.segments[-1].arrive == datetime(2030, 3, 2, 15, 25)
    assert emirates.origin == "LHR" and emirates.destination == "PEK"
    assert [s.flight_number for s in emirates.segments] == ["EK30", "EK306"]
    assert emirates.total_minutes == 935
    assert result.latency_ms is not None


def test_top_results_are_not_lost() -> None:
    assert len(google_payload.parse_callback(callback())) == 11
    assert len(parse_js(callback())) == 6


def test_side_channel_commas_do_not_break_json() -> None:
    # Synthetic reproduction of the split/rsplit flaw, not the lost char235 body.
    script = callback(side='{"metadata":[1,2],"text":"data:, comma"}')
    with pytest.raises(json.JSONDecodeError, match="Extra data"):
        parse_js(script)
    assert len(google_payload.parse_callback(script)) == 11


@pytest.mark.parametrize(
    "script",
    [
        callback() + "[]",
        callback(side="[]"),
        callback(side="{} extra"),
        callback().replace(", sideChannel:", ", unknown:"),
        callback(payload="{}"),
        callback(payload="[]"),
        callback().replace("ds:1", "ds:9"),
        callback(payload="[truncated"),
    ],
)
def test_unknown_or_malformed_envelope_fails_closed(script: str) -> None:
    with pytest.raises(ValueError, match=r"google|Expecting"):
        google_payload.parse_callback(script)


@pytest.mark.parametrize("html", ["<html>blocked</html>", "<script>other data</script>"])
def test_missing_callback_fails(html: str) -> None:
    with pytest.raises(ValueError, match="missing"):
        google_payload.parse_html(html)


def test_duplicate_callback_fails() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        google_payload.parse_html(("<script>" + callback() + "</script>") * 2)
