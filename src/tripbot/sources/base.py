"""What every flight source looks like to the nightly run."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tripbot.domain.searches import SearchKey, SearchResult

LOGO_URL = "https://www.gstatic.com/flights/airline_logos/70px/{code}.png"


class FlightSource(Protocol):
    name: str
    shape: SearchShape

    def search(self, key: SearchKey) -> SearchResult: ...


@dataclass(frozen=True)
class SearchShape:
    """How a source wants its airports: several per side, or one city code."""

    many_airports: bool
    city_codes: bool


def timed(search: Callable[[], SearchResult]) -> SearchResult:
    """Run a search and stamp its latency. A source failure never stops the run."""
    started = time.monotonic()
    try:
        result = search()
    except Exception as error:  # every source is untrusted I/O; record and move on
        result = SearchResult(status="error", error=f"{type(error).__name__}: {error}"[:300])
    latency = int((time.monotonic() - started) * 1000)
    return SearchResult(
        status=result.status, offers=result.offers, error=result.error, latency_ms=latency
    )


def logo_for(airline_code: str) -> str | None:
    return LOGO_URL.format(code=airline_code) if len(airline_code) == 2 else None
