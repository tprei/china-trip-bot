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
def conn(db: sqlite3.Connection) -> sqlite3.Connection:
    return db


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


def test_a_failing_source_is_announced_once(
    config: TripConfig, reference: Reference, conn: sqlite3.Connection
) -> None:
    reports = [
        run_nightly(
            nightly(config, reference, conn, FakeSource(PRICES), BrokenSource()),
            NIGHT_ONE + timedelta(days=night),
        )
        for night in range(3)
    ]
    assert reports[1].health["tripcom"] == (0, 3)
    assert [report.newly_failing for report in reports] == [[], ["tripcom"], []]
    assert [report.failing_sources for report in reports] == [[], ["tripcom"], ["tripcom"]]
    assert reports[2].ok


class RedundantSource(FakeSource):
    def __init__(self, name: str, fails: bool) -> None:
        super().__init__(PRICES)
        self.name = name
        self.fails = fails
        self.shape = SearchShape(many_airports=False, city_codes=name == "tripcom")

    def search(self, key: SearchKey) -> SearchResult:
        if self.fails:
            self.calls += 1
            if self.name == "google":
                raise ValueError("invalid upstream response")
            return SearchResult(status="error", error="HTTP 430")
        result = super().search(key)
        return replace(result, offers=tuple(replace(o, source=self.name) for o in result.offers))


@pytest.mark.parametrize(
    ("google_fails", "tripcom_fails"), [(True, False), (False, True), (False, False), (True, True)]
)
def test_both_sources_are_always_collected_with_separate_budgets(
    config: TripConfig,
    reference: Reference,
    conn: sqlite3.Connection,
    google_fails: bool,
    tripcom_fails: bool,
) -> None:
    google_source = RedundantSource("google", google_fails)
    tripcom_source = RedundantSource("tripcom", tripcom_fails)
    deps = nightly(config, reference, conn, google_source, tripcom_source)
    deps.config = replace(config, budgets=Budgets(google=2, serpapi=0, tripcom=3))
    report = run_nightly(deps, NIGHT_ONE)
    assert (google_source.calls, tripcom_source.calls) == (2, 3)
    assert report.health == {
        "google": (0 if google_fails else 2, 2),
        "tripcom": (0 if tripcom_fails else 3, 3),
    }
    assert report.ok == (not google_fails or not tripcom_fails)
    rows = conn.execute("SELECT DISTINCT source FROM offers").fetchall()
    assert {row[0] for row in rows} == {
        name
        for name, failed in (("google", google_fails), ("tripcom", tripcom_fails))
        if not failed
    }
