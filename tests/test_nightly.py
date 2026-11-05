import sqlite3
from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest
from factories import eur, offer

from tripbot.config import Budgets, TripConfig
from tripbot.domain.flights import Offer
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.pipeline.nightly import NightlyDeps, run_nightly
from tripbot.reference import Reference
from tripbot.sources.base import FlightSource, SearchShape
from tripbot.store import repo
from tripbot.store.db import connect

NIGHT_ONE = datetime(2026, 11, 6, 3, 0)
HOME = {"thiago": "DUB", "lon": "LHR", "sao": "GRU"}


class FakeSource:
    """Answers every key with one direct flight per China airport."""

    name = "serpapi"
    shape = SearchShape(many_airports=True, city_codes=False)

    def __init__(self, prices: dict[str, int]) -> None:
        self.prices = prices
        self.calls = 0

    def search(self, key: SearchKey) -> SearchResult:
        self.calls += 1
        depart = datetime.combine(key.travel_date, time(9, 0))
        home = HOME[key.group_id]
        price = eur(self.prices[key.group_id])
        offers: list[Offer] = []
        for airport in ("PEK", "CAN"):
            path = [home, airport] if key.direction == "outbound" else [airport, home]
            offers.append(offer(key.group_id, key.direction, path, depart, price))
        return SearchResult(status="ok", offers=tuple(offers))


class BrokenSource:
    name = "tripcom"
    shape = SearchShape(many_airports=False, city_codes=True)

    def search(self, key: SearchKey) -> SearchResult:
        return SearchResult(status="error", error="HTTP 432")


@pytest.fixture
def conn() -> sqlite3.Connection:
    return connect(":memory:")


def nightly(
    config: TripConfig, reference: Reference, conn: sqlite3.Connection, *sources: FlightSource
) -> NightlyDeps:
    budgets = Budgets(google=0, serpapi=1000, tripcom=3)
    return NightlyDeps(
        config=replace(config, budgets=budgets),
        reference=reference,
        conn=conn,
        sources=list(sources),
        log=lambda _: None,
    )


PRICES = {"thiago": 300, "lon": 280, "sao": 700}


def test_a_night_ranks_every_window_and_snapshots_it(
    config: TripConfig, reference: Reference, conn: sqlite3.Connection
) -> None:
    source = FakeSource(PRICES)
    report = run_nightly(nightly(config, reference, conn, source), NIGHT_ONE)

    assert source.calls == 54
    assert report.health == {"serpapi": (54, 54)}
    assert report.ok and report.alerts == []
    for window in config.windows:
        assert report.rankings.main[window.id].best is not None
        assert len(repo.snapshots(conn, window.id)) == 1
        assert len(repo.snapshots(conn, window.id, "no-shanghai")) == 1


def test_a_cheaper_night_sends_a_record_low_alert(
    config: TripConfig, reference: Reference, conn: sqlite3.Connection
) -> None:
    run_nightly(nightly(config, reference, conn, FakeSource(PRICES)), NIGHT_ONE)
    cheaper = FakeSource({**PRICES, "sao": 600})
    report = run_nightly(nightly(config, reference, conn, cheaper), NIGHT_ONE + timedelta(days=1))

    assert {alert.window_id for alert in report.alerts} == {"A", "B", "C"}
    assert all(alert.kind == "record_low" for alert in report.alerts)
    assert all(alert.saving == eur(100 * 2 * 2) for alert in report.alerts)


def test_a_source_failing_two_nights_is_reported(
    config: TripConfig, reference: Reference, conn: sqlite3.Connection
) -> None:
    for night in range(2):
        report = run_nightly(
            nightly(config, reference, conn, FakeSource(PRICES), BrokenSource()),
            NIGHT_ONE + timedelta(days=night),
        )
    assert report.health["tripcom"] == (0, 3)
    assert report.failing_sources == ["tripcom"]
    assert report.ok
