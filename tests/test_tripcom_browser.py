from collections.abc import Callable
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from playwright.sync_api import Browser, BrowserType, Page, sync_playwright

from tripbot.domain.roundtrip import RoundTripRequest
from tripbot.sources.tripcom_browser import (
    TripComBrowserSource,
    clean_url,
    group_total,
    minutes,
    money,
    selected_fare,
    selected_journeys,
)


def test_evidence_url_strips_credentials_query_fragment() -> None:
    assert (
        clean_url("https://user:secret@us.trip.com/flights/?token=secret#private")
        == "https://us.trip.com/flights/"
    )


@pytest.mark.parametrize("options", [0, 4, -1])
def test_bounded_options(tmp_path: Path, options: int) -> None:
    with pytest.raises(ValueError, match="headed browser required"):
        TripComBrowserSource(tmp_path, max_options=options)


def test_headed_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="headed browser required"):
        TripComBrowserSource(tmp_path, headed=False)


def test_visible_price_not_raw_fractional_price() -> None:

    assert money("€\n841").minor == 84100
    assert money("€1,725").minor == 172500


def test_duration_uses_observed_flight_time() -> None:

    assert minutes("Flight time: 7h 25m") == 445
    assert minutes("3h in Dubai") == 180
    with pytest.raises(ValueError, match="Missing observed"):
        minutes("unknown")


def test_startup_failure_is_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = TripComBrowserSource(tmp_path)

    def fail(request: RoundTripRequest) -> None:
        raise RuntimeError("private runtime detail")

    monkeypatch.setattr(source, "_search", fail)
    result = source.search(
        RoundTripRequest("g", "DUB", "PEK", date(2027, 8, 29), date(2027, 9, 10), 1)
    )
    assert result.status == "error"
    assert result.error == "Browser startup/runtime failure: RuntimeError"


def test_group_total_must_be_observed() -> None:
    assert group_total("Total\n€1,682").minor == 168200
    with pytest.raises(ValueError, match="refusing to multiply"):
        group_total("€841 per person, 2 adults")
    with pytest.raises(ValueError, match="ambiguous"):
        group_total("Total €1,682 Total €1,800")


def test_observed_group_rounding_is_not_multiplication() -> None:
    text = "€1,443\nRound-trip\nTotal €2,885\nEconomy class"
    assert money(text).minor == 144300
    assert group_total(text).minor == 288500
    assert group_total(text).minor != money(text).minor * 2


def test_selected_dom_preserves_two_journeys_and_observed_total() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content("""
        <div class="m-flightinfo__simplehead-wrapper">
          <div class="flight-info-expand">
            <span class="port-code">DUB</span><span class="port-code">PEK</span>
            <span data-testid="flight-time-2027-08-29 09:00:00">9 AM</span>
            <span data-testid="flight-time-2027-08-30 03:00:00">3 AM</span>
            <span class="flight-info-flightNo">CA123</span>
            <span class="flight-info-spend-time">10h</span>
            <span class="flight-info-airline-name">Air China</span>
          </div>
        </div>
        <div class="m-flightinfo__simplehead-wrapper">
          <div class="flight-info-expand">
            <span class="port-code">PEK</span><span class="port-code">DUB</span>
            <span data-testid="flight-time-2027-09-10 09:00:00">9 AM</span>
            <span data-testid="flight-time-2027-09-10 12:00:00">12 PM</span>
            <span class="flight-info-flightNo">CA124</span>
            <span class="flight-info-spend-time">11h</span>
            <span class="flight-info-airline-name">Air China</span>
          </div>
        </div>
        <div data-testid="mid_result_item_fare">Student Economy
          <span data-testid="u_price_info">€700</span>Total €1,400</div>
        <div data-testid="mid_result_item_fare">Economy
          <span data-testid="u_price_info">€841</span>Total €1,681</div>
        <div data-testid="mid_result_item_fare">TripFlex Economy
          <span data-testid="u_price_info">€800</span>Total €1,600</div>
        """)
        journeys = selected_journeys(page, [600, 660])
        assert journeys[0].origin == "DUB"
        assert journeys[1].destination == "DUB"
        assert journeys[0].segments[0].flight_number == "CA123"
        assert journeys[0].total_minutes == 600
        assert not journeys[0].self_transfer
        page.locator(".m-flightinfo__simplehead-wrapper").first.evaluate(
            "e => e.append(document.createTextNode('Self-transfer'))"
        )
        assert selected_journeys(page, [600, 660])[0].self_transfer
        assert group_total(selected_fare(page).inner_text()).minor == 168100
        page.set_content("<body>No fares</body>")
        with pytest.raises(ValueError, match="precisely two"):
            selected_journeys(page, [600, 660])
        with pytest.raises(ValueError, match="No unrestricted"):
            selected_fare(page)
        browser.close()


def test_explicit_block_stops_before_results(tmp_path: Path) -> None:
    source = TripComBrowserSource(tmp_path)
    request = RoundTripRequest("g", "DUB", "PEK", date(2027, 8, 29), date(2027, 9, 10), 1)

    def blocked_form(page: Page, request: RoundTripRequest, capture: Callable[[str], None]) -> None:
        page.set_content("<body>Verify you are human</body>")
        capture("blocked-local-fixture")

    launch = BrowserType.launch

    def local_launch(engine: BrowserType, *, headless: bool) -> Browser:
        return launch(engine, headless=True)

    with (
        patch.object(BrowserType, "launch", local_launch),
        patch.object(source, "_form", side_effect=blocked_form),
        patch.object(source, "_results") as results,
    ):
        result = source.search(request)
    assert result.status == "error"
    assert result.error and "explicit_block" in result.error
    results.assert_not_called()
    assert len(list(tmp_path.glob("*/evidence.json"))) == 1
