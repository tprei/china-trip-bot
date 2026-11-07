"""Run-scoped API circuit and optional, bounded joint-fare browser redundancy.

The direct endpoint only has a validated one-way parser. A valid API response
never launches the browser and is not misrepresented as a joint fare.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Protocol

from tripbot.domain.roundtrip import RoundTripRequest, RoundTripResult
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.sources.tripcom import TripCom


class RoundTripBrowser(Protocol):
    def search(self, request: RoundTripRequest) -> RoundTripResult: ...


class TripComFallback:
    """Create once per execution; failed API is not retried for every group."""

    def __init__(
        self,
        api: TripCom,
        browser: RoundTripBrowser | None,
        *,
        budget: int = 3,
        spacing_seconds: float = 4,
    ) -> None:
        if budget < 0 or spacing_seconds < 4:
            raise ValueError("nonnegative budget and at least 4s spacing required")
        self.api = api
        self.browser = browser
        self.remaining = budget
        self.spacing_seconds = spacing_seconds
        self._last_browser = 0.0
        self.last_api_result: SearchResult | None = None

    def search(self, request: RoundTripRequest) -> RoundTripResult:
        if self.remaining <= 0:
            return RoundTripResult("error", error="Trip.com execution budget exhausted")
        self.remaining -= 1
        key = SearchKey(
            "tripcom",
            request.group_id,
            "outbound",
            (request.origin,),
            (request.destination,),
            request.outbound_date,
        )
        api_result = self.api.search(key)
        self.last_api_result = api_result
        if api_result.status != "error":
            return RoundTripResult(
                "empty",
                diagnostics=(
                    f"API {api_result.status}; browser not invoked",
                    "API one-way offers remain separate; no validated joint API fare",
                ),
            )
        diagnostic = f"API unavailable: {api_result.error}"
        if self.browser is None:
            return RoundTripResult(
                "error", error="Browser fallback disabled", diagnostics=(diagnostic,)
            )
        delay = self._last_browser + self.spacing_seconds - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last_browser = time.monotonic()
        try:
            result = self.browser.search(request)
            if any(quote.request != request for quote in result.quotes):
                raise ValueError("browser quote does not match requested passengers/route")
        except Exception as error:  # source boundary, preserve the next group
            result = RoundTripResult(
                "error", error=f"Browser {type(error).__name__}: {error}"[:300]
            )
        return replace(result, diagnostics=(diagnostic, *result.diagnostics))
