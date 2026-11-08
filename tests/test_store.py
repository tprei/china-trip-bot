import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from factories import eur, offer

from tripbot.domain.money import Money, Rates
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.store import repo
from tripbot.store.db import connect

NOW = datetime(2026, 11, 6, 3, 0)
KEY = SearchKey(
    source="google",
    group_id="lon",
    direction="outbound",
    origins=("LHR",),
    destinations=("PEK",),
    travel_date=date(2027, 8, 29),
)


@pytest.fixture
def conn(db: sqlite3.Connection) -> sqlite3.Connection:
    return db


def test_offers_round_trip_through_the_store(conn: sqlite3.Connection) -> None:
    found = offer("lon", "outbound", ["LHR", "DOH", "PEK"], datetime(2027, 8, 29, 9), eur(520))
    run = repo.start_run(conn, NOW)
    repo.record_search(conn, run, KEY, SearchResult(status="ok", offers=(found,)), NOW)

    loaded = repo.latest_offers(conn, since=NOW - timedelta(days=1))
    assert loaded == [found]
    assert loaded[0].found_on == NOW.date()
    assert repo.last_searched(conn) == {KEY: NOW}


def test_only_the_newest_successful_search_counts(conn: sqlite3.Connection) -> None:
    old = offer("lon", "outbound", ["LHR", "PEK"], datetime(2027, 8, 29, 9), eur(600))
    new = offer("lon", "outbound", ["LHR", "PEK"], datetime(2027, 8, 29, 9), eur(550))
    later = NOW + timedelta(days=1)
    repo.record_search(conn, None, KEY, SearchResult(status="ok", offers=(old,)), NOW)
    repo.record_search(conn, None, KEY, SearchResult(status="ok", offers=(new,)), later)
    repo.record_search(
        conn, None, KEY, SearchResult(status="error", error="blocked"), later + timedelta(1)
    )
    assert [o.price for o in repo.latest_offers(conn, since=NOW)] == [eur(550)]


def test_stale_searches_are_dropped(conn: sqlite3.Connection) -> None:
    found = offer("lon", "outbound", ["LHR", "PEK"], datetime(2027, 8, 29, 9), eur(600))
    repo.record_search(conn, None, KEY, SearchResult(status="ok", offers=(found,)), NOW)
    assert repo.latest_offers(conn, since=NOW + timedelta(days=1)) == []


def test_failed_runs_are_counted_per_source(conn: sqlite3.Connection) -> None:
    for day in range(3):
        run = repo.start_run(conn, NOW + timedelta(days=day))
        status = "ok" if day == 0 else "error"
        repo.record_search(conn, run, KEY, SearchResult(status=status), NOW)
    assert repo.consecutive_failed_runs(conn, "google") == 2
    last = repo.last_run_id(conn)
    assert last is not None and repo.source_health(conn, last) == {"google": (0, 1)}


def test_rates_keep_the_latest_day(conn: sqlite3.Connection) -> None:
    repo.save_rates(conn, Rates({"GBP": Decimal("0.86")}, date(2026, 11, 4)))
    repo.save_rates(conn, Rates({"GBP": Decimal("0.87")}, date(2026, 11, 5)))
    latest = repo.latest_rates(conn)
    assert latest is not None and latest.per_eur["GBP"] == Decimal("0.87")


def test_todos_are_added_and_completed(conn: sqlite3.Connection) -> None:
    first = repo.add_todo(conn, "Apply for L visas", "bot", NOW)
    repo.add_todo(conn, "Book sleepers", "Thiago", NOW)
    assert repo.complete_todo(conn, first, "Luísa", NOW)
    assert not repo.complete_todo(conn, first, "Luísa", NOW)
    assert [todo.text for todo in repo.todos(conn)] == ["Book sleepers"]
    assert len(repo.todos(conn, include_done=True)) == 2


def test_summaries_and_alerts(conn: sqlite3.Connection) -> None:
    totals = {"A": Money(612000, "EUR"), "B": Money(565100, "EUR")}
    repo.record_summary(conn, "2026-W45", totals, {"A": "k-a", "B": "k-b", "C": "k-c"}, NOW)
    summary = repo.last_summary(conn)
    assert summary is not None and summary.week == "2026-W45"
    assert summary.comparable({"A": "k-a", "B": "edited", "C": "k-c"}) == {"A": totals["A"]}
    repo.record_alert(conn, "record_low", "B", Money(542000, "EUR"), NOW)
    assert repo.alerted_on(conn, "B", NOW.date())
    assert not repo.alerted_on(conn, "A", NOW.date())


def test_reopening_does_not_rerun_migrations(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    first = connect(path)
    repo.set_setting(first, "target_eur", "5000")
    first.close()
    again = connect(path)
    assert repo.get_setting(again, "target_eur") == "5000"
    again.close()
