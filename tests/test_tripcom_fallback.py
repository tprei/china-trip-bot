"""No network: API health, circuit, browser boundary and budgets."""

from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from test_roundtrip import quote

from tripbot.domain.roundtrip import RoundTripRequest, RoundTripResult
from tripbot.domain.trip import Group
from tripbot.sources.tripcom import TripCom
from tripbot.sources.tripcom_fallback import TripComFallback

REQUEST = RoundTripRequest("lon", "LHR", "PEK", date(2027, 8, 28), date(2027, 9, 10), 2)


class Browser:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[RoundTripRequest] = []
        self.fail = fail

    def search(self, request: RoundTripRequest) -> RoundTripResult:
        self.requests.append(request)
        if self.fail:
            raise ValueError("explicit browser denial")
        return RoundTripResult("empty")


@pytest.mark.parametrize("status", [200, 432, 429, 500])
def test_api_health_and_circuit(status: int) -> None:
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text='data: {"itineraryList": []}')

    browser = Browser()
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        source = TripComFallback(TripCom(client), browser)
        with patch("time.sleep"):
            results = [source.search(REQUEST), source.search(REQUEST)]
    assert all(result.status == "empty" for result in results)
    assert len(calls) == (2 if status == 200 else 1)
    assert browser.requests == ([] if status == 200 else [REQUEST, REQUEST])
    if status != 200:
        assert "circuit open" in results[1].diagnostics[0]


@pytest.mark.parametrize("browser", [None, Browser(fail=True)])
def test_browser_error(browser: Browser | None) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(432))) as client:
        result = TripComFallback(TripCom(client), browser).search(REQUEST)
    assert result.status == "error"
    assert result.error and "Browser" in result.error
    assert "HTTP 432" in result.diagnostics[0]


def test_budget_prevents_all_io() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network"))) as client:
        result = TripComFallback(TripCom(client), Browser(), budget=0).search(REQUEST)
    assert result.status == "error"
    assert result.error and "budget" in result.error


def test_schema_failure_opens_circuit() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"error": "denied"}))
    ) as client:
        api = TripCom(client)
        result = TripComFallback(api, Browser()).search(REQUEST)
    assert result.status == "empty"
    assert api.api_failure == "API schema missing itineraryList"


@pytest.mark.parametrize(("budget", "spacing"), [(-1, 4), (1, 0)])
def test_invalid_limits(budget: int, spacing: float) -> None:
    with httpx.Client() as client, pytest.raises(ValueError, match="budget"):
        TripComFallback(TripCom(client), None, budget=budget, spacing_seconds=spacing)


def test_browser_success_keeps_joint_quote_and_pax() -> None:
    observed = quote(Group("lon", "LON", ("A", "B")))
    request = observed.request
    browser = Browser()
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(432))) as client,
        patch.object(browser, "search", return_value=RoundTripResult("ok", (observed,))),
    ):
        source = TripComFallback(TripCom(client), browser)
        result = source.search(request)
        assert result.quotes == (observed,)
        assert result.quotes[0].total.minor == 20001
        assert result.quotes[0].request.adults == 2
        with patch("time.sleep") as sleep:
            result = source.search(replace(request, adults=1))
        assert result.status == "error"
        assert result.error and "passengers/route" in result.error
        sleep.assert_called_once()


def test_valid_api_offers_do_not_start_browser() -> None:
    fixture = Path(__file__).parent / "fixtures" / "tripcom-lon-bjs.sse"
    browser = Browser()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=fixture.read_text()))
    ) as client:
        source = TripComFallback(TripCom(client), browser)
        result = source.search(REQUEST)
    assert result.status == "empty"
    assert source.last_api_result and source.last_api_result.status == "ok"
    assert source.last_api_result.offers
    assert not browser.requests
    assert "not invoked" in result.diagnostics[0]
