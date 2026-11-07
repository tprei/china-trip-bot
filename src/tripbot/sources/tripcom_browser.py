"""Bounded, ordinary headed Trip.com UI fallback; never proceeds to booking."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Locator, Page, sync_playwright

from tripbot.domain.flights import Segment
from tripbot.domain.money import Money
from tripbot.domain.roundtrip import Journey, RoundTripQuote, RoundTripRequest, RoundTripResult


def clean_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{parts.path}"


class TripComBrowserSource:
    def __init__(self, evidence_dir: Path, headed: bool = True, max_options: int = 1) -> None:
        if not headed or not 1 <= max_options <= 3:
            raise ValueError("headed browser required; max_options must be 1..3")
        self.evidence_dir = Path(evidence_dir)
        self.max_options = max_options  # Upper bound; this conservative adapter samples one pair.

    def search(self, request: RoundTripRequest) -> RoundTripResult:
        try:
            return self._search(request)
        except Exception as exc:
            return RoundTripResult(
                "error", error=f"Browser startup/runtime failure: {type(exc).__name__}"
            )

    def _search(self, request: RoundTripRequest) -> RoundTripResult:
        directory = self.evidence_dir / (
            re.sub(r"[^A-Za-z0-9_-]", "_", request.group_id)
            + "-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        )
        directory.mkdir(parents=True)
        network: list[dict[str, object]] = []
        steps: list[dict[str, str]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.set_default_timeout(30000)

            def response(r: Any) -> None:
                if r.request.resource_type == "document" or r.status >= 400:
                    network.append(
                        {
                            "url": clean_url(r.url),
                            "status": r.status,
                            "gate": bool(r.headers.get("x-gate-error-cause")),
                            "resource_type": r.request.resource_type,
                        }
                    )

            page.on("response", response)

            def capture(name: str) -> None:
                steps.append(
                    {"name": name, "utc": datetime.now(UTC).isoformat(), "url": clean_url(page.url)}
                )
                text = page.locator("body").inner_text()
                (directory / (name + ".txt")).write_text(text)
                aria = re.sub(
                    r"(/url: [^?\n]+)\?[^\n]*", r"\1", page.locator("body").aria_snapshot()
                )
                (directory / (name + "-aria.txt")).write_text(aria)
                page.screenshot(path=str(directory / (name + ".png")))
                if any(n["status"] in (403, 429, 430, 432) or n["gate"] for n in network):
                    raise RuntimeError("explicit_block: HTTP denial; no retry")
                if re.search(
                    r"verify you are human|access denied|complete the captcha", text, re.IGNORECASE
                ):
                    raise RuntimeError("explicit_block: human verification; no retry")

            result = RoundTripResult("error", error="UI flow incomplete")
            try:
                self._form(page, request, capture)
                result = self._results(page, request, directory, capture)
            except Exception as exc:
                message = re.sub(r"https?://\S+", "[url]", str(exc).split("Call log:")[0])[:600]
                if any(n["status"] in (403, 429, 430, 432) or n["gate"] for n in network):
                    message = "explicit_block: HTTP denial; no retry"
                result = RoundTripResult("error", error=message)
                with suppress(Exception):
                    capture("error")
            finally:
                (directory / "evidence.json").write_text(
                    json.dumps(
                        {
                            "request": asdict(request),
                            "headed": True,
                            "stock_playwright": True,
                            "network": network,
                            "steps": steps,
                            "result": asdict(result),
                            "finished_utc": datetime.now(UTC).isoformat(),
                        },
                        default=str,
                        indent=2,
                    )
                )
                context.close()
                browser.close()
        return result

    def _form(self, page: Page, request: RoundTripRequest, capture: Callable[[str], None]) -> None:
        page.goto("https://www.trip.com/", wait_until="domcontentloaded", timeout=45000)
        page.get_by_role("tab", name="Flights", exact=True).wait_for()
        capture("01-home")
        page.get_by_role("tab", name="Flights", exact=True).click()
        page.get_by_role("button", name="Language/Currency", exact=True).click()
        page.get_by_role("tab", name="Currency", exact=True).click()
        with page.expect_navigation(wait_until="domcontentloaded", timeout=45000):
            page.locator('.top-list [role="gridcell"][aria-label="Euro (€)"]').click()
        page.get_by_role("tab", name="Flights", exact=True).click()
        page.get_by_role("radio", name="Round-trip", exact=True).wait_for()
        capture("01-eur-flights")
        for side, code in (("from", request.origin), ("to", request.destination)):
            label = "Departure airport or city" if side == "from" else "Arrival airport or city"
            page.get_by_role("group", name=label, exact=True).click()
            page.locator(f".nh_poi-input input[data-testid=search_city_{side}0]").fill(code)
            suggestion = page.get_by_role(
                "button", name=re.compile(r".*\b" + code + r"\b.*Airport")
            )
            if code in ("LHR", "GRU"):
                suggestion.click()
            else:
                page.get_by_role("listitem").filter(
                    has_text=re.compile(r"\b" + code + r"\b")
                ).click()
        page.get_by_role("group", name=re.compile("^Depart:")).click()
        for target in (request.outbound_date, request.return_date):
            label = target.strftime("%A, %B ") + str(target.day) + target.strftime(", %Y,")
            cell = page.get_by_role("gridcell", name=re.compile("^" + re.escape(label)))
            for _ in range(18):
                if cell.count():
                    break
                page.get_by_role("button", name="Go to next month", exact=True).click()
            cell.click()
        if request.adults != 1:
            page.get_by_role("button", name="1 adult, Economy", exact=True).click()
            capture("02-passengers")
            self._adults(page, request.adults)
            done = page.get_by_role("button", name="Done", exact=True)
            if done.count():
                done.click()
            else:
                page.get_by_role("heading", name="Your next take-off awaits", exact=True).click()
        page.get_by_role(
            "button",
            name=f"{request.adults} adult{'s' if request.adults != 1 else ''}, Economy",
            exact=True,
        ).wait_for()
        capture("03-ready")
        page.get_by_text("Search", exact=True).click()
        page.get_by_role("button", name=re.compile("^Select this (?:fare|flight)$")).first.wait_for(
            timeout=90000
        )
        capture("04-outbound")

    def _adults(self, page: Page, adults: int) -> None:
        for _ in range(adults - 1):
            page.get_by_test_id("AdultCount_plus").click()
        page.get_by_role("spinbutton", name=f"{adults} adults", exact=True).wait_for()

    def _results(
        self, page: Page, request: RoundTripRequest, directory: Path, capture: Callable[[str], None]
    ) -> RoundTripResult:
        durations = []
        for stage in ("outbound", "return"):
            page.get_by_role("tab", name="Cheapest", exact=True).click()
            cards = page.get_by_role("group", name=re.compile("^Flight departing from"))
            cards.first.wait_for()
            chosen = None
            # First cheapest-sorted eligible rendered card, not arbitrary ambiguous match.
            for index in range(cards.count()):
                card = cards.nth(index)
                transfer = card.get_by_test_id("stopInfoText").inner_text()
                if (
                    card.get_by_test_id("stopDot").count() <= 1
                    and ("Nonstop" in transfer or minutes(transfer) <= 480)
                    and "Self-transfer" not in card.inner_text()
                ):
                    chosen = card
                    break
            if chosen is None:
                return RoundTripResult("empty", diagnostics=("No eligible rendered result card",))
            durations.append(minutes(chosen.get_by_test_id("flightInfoDuration").inner_text()))
            capture("05-" + stage + "-sorted")
            container = chosen.locator(
                'xpath=ancestor::*[.//button[@aria-label="Select this fare" '
                'or @aria-label="Select this flight"]][1]'
            )
            container.get_by_role(
                "button", name=re.compile("^Select this (?:fare|flight)$")
            ).click()
            if stage == "outbound":
                page.wait_for_url(re.compile("ShowFareNext", re.IGNORECASE), timeout=60000)
                page.get_by_role(
                    "button", name=re.compile("^Select this (?:fare|flight)$")
                ).first.wait_for(timeout=90000)
            else:
                page.get_by_test_id("mid_result_item_fare").first.wait_for(timeout=60000)
        for target in (request.outbound_date, request.return_date):
            label = target.strftime("%a, %b ") + str(target.day) + target.strftime(", %Y")
            page.locator(".title-info-wrapper").filter(has_text=label).click()
        page.locator(".flight-info-expand:visible").first.wait_for()
        capture("06-full-itinerary")
        journeys = selected_journeys(page, durations)
        fare = selected_fare(page)
        price = fare.get_by_test_id("u_price_info")
        per_person = money(price.inner_text())
        raw = {
            "data_price": price.get_attribute("data-price"),
            "data_total_price": price.get_attribute("data-total-price"),
            "visible_per_person": price.inner_text(),
        }
        (directory / "observed-prices.json").write_text(json.dumps(raw, indent=2))
        if request.adults == 1:
            total = per_person
        else:
            # Total is visibly printed on each fare tile for a multi-adult search.
            # Scope strictly to the selected unrestricted fare, not other fare totals.
            total = group_total(fare.inner_text())
            raw["visible_total_minor"] = str(total.minor)
            capture("07-group-fare")
        (directory / "observed-prices.json").write_text(json.dumps(raw, indent=2))
        quote = RoundTripQuote(
            request,
            journeys[0],
            journeys[1],
            total,
            per_person,
            datetime.now(UTC),
            "tripcom-browser",
            clean_url(page.url),
            fare.inner_text() + "; visible rounded live fare, not payment-page final price",
        )
        return RoundTripResult("ok", (quote,), diagnostics=(str(directory),))


def minutes(text: str) -> int:
    hours = re.search(r"(\d+)\s*h", text)
    mins = re.search(r"(\d+)\s*m", text)
    value = (int(hours[1]) * 60 if hours else 0) + (int(mins[1]) if mins else 0)
    if not value:
        raise ValueError("Missing observed duration")
    return value


def money(text: str) -> Money:
    match = re.search(r"€\s*([\d,]+(?:\.\d{2})?)", text)
    if not match:
        raise ValueError("Missing visible EUR price")
    return Money.of(match[1].replace(",", ""), "EUR")


def group_total(text: str) -> Money:
    matches = re.findall(r"Total(?: price)?\s*€\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    if len(set(matches)) != 1:
        raise ValueError("Observed group total missing or ambiguous; refusing to multiply")
    return Money.of(matches[0].replace(",", ""), "EUR")


def selected_journeys(page: Page, durations: list[int]) -> list[Journey]:
    wrappers = page.locator(".m-flightinfo__simplehead-wrapper")
    journeys = []
    if wrappers.count() != 2:
        raise ValueError("Expected precisely two selected journeys")
    for index in range(2):
        segments = []
        for node in wrappers.nth(index).locator(".flight-info-expand:visible").all():
            ports = node.locator(".port-code").all_text_contents()
            times = node.locator('[data-testid^="flight-time-"]').evaluate_all(
                "es => es.map(e => e.getAttribute('data-testid').slice(12))"
            )
            number = node.locator(".flight-info-flightNo").inner_text().strip()
            if len(ports) != 2 or len(times) != 2:
                raise ValueError("Ambiguous segment endpoints")
            segments.append(
                Segment(
                    ports[0],
                    ports[1],
                    datetime.fromisoformat(times[0]),
                    datetime.fromisoformat(times[1]),
                    minutes(node.locator(".flight-info-spend-time").inner_text()),
                    number[:2],
                    node.locator(".flight-info-airline-name").inner_text(),
                    number,
                )
            )
        journey = Journey(
            tuple(segments),
            durations[index],
            self_transfer=bool(
                re.search(r"self[- ]transfer", wrappers.nth(index).inner_text(), re.IGNORECASE)
            ),
        )
        if journey.stops > 1 or any(lay.minutes > 480 for lay in journey.layovers):
            raise ValueError("Expanded itinerary violates bounded connection policy")
        journeys.append(journey)
    return journeys


def selected_fare(page: Page) -> Locator:
    fares = page.get_by_test_id("mid_result_item_fare")
    eligible = []
    for fare in fares.all():
        text = fare.inner_text()
        if "Economy" in text and not re.search(
            "Student|TripFlex|Members only", text, re.IGNORECASE
        ):
            price = fare.get_by_test_id("u_price_info")
            if price.count() == 1:
                eligible.append((money(price.inner_text()).minor, fare))
    if not eligible:
        raise ValueError("No unrestricted economy fare")
    _, fare = min(eligible, key=lambda pair: pair[0])
    return fare
