"""Queries. Every function takes an open connection and plain domain data."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tripbot.domain.flights import Direction, Offer, Segment
from tripbot.domain.money import Money, Rates
from tripbot.domain.searches import SearchKey, SearchResult

DIRECTIONS: dict[str, Direction] = {"outbound": "outbound", "return": "return"}


# Runs -----------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, now: datetime) -> int:
    with conn:
        cursor = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now.isoformat(),))
    return _row_id(cursor)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, now: datetime) -> None:
    with conn:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, now.isoformat(), run_id),
        )


# Searches and offers ----------------------------------------------------------


def record_search(
    conn: sqlite3.Connection,
    run_id: int | None,
    key: SearchKey,
    result: SearchResult,
    now: datetime,
) -> int:
    with conn:
        cursor = conn.execute(
            """INSERT INTO searches (run_id, source, group_id, direction, origins, destinations,
                   travel_date, status, result_count, error, latency_ms, searched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                key.source,
                key.group_id,
                key.direction,
                ",".join(key.origins),
                ",".join(key.destinations),
                key.travel_date.isoformat(),
                result.status,
                len(result.offers),
                result.error,
                result.latency_ms,
                now.isoformat(),
            ),
        )
        search_id = _row_id(cursor)
        conn.executemany(
            """INSERT INTO offers (search_id, source, group_id, direction, segments_json,
                   price_minor, currency, total_minutes, self_transfer, booking_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    search_id,
                    offer.source,
                    offer.group_id,
                    offer.direction,
                    json.dumps([_segment_json(s) for s in offer.segments]),
                    offer.price.minor,
                    offer.price.currency,
                    offer.total_minutes,
                    int(offer.self_transfer),
                    offer.booking_url,
                )
                for offer in result.offers
            ],
        )
    return search_id


def last_searched(conn: sqlite3.Connection) -> dict[SearchKey, datetime]:
    """When each key was last searched successfully or not."""
    rows = conn.execute(
        """SELECT source, group_id, direction, origins, destinations, travel_date,
                  MAX(searched_at) AS last
           FROM searches GROUP BY source, group_id, direction, origins, destinations, travel_date"""
    ).fetchall()
    return {_key(row): datetime.fromisoformat(row["last"]) for row in rows}


def latest_offers(conn: sqlite3.Connection, since: datetime) -> list[Offer]:
    """Offers from the newest successful search of every key, if newer than `since`."""
    rows = conn.execute(
        """SELECT o.*, s.searched_at FROM offers o JOIN searches s ON s.id = o.search_id
           WHERE s.id IN (
               SELECT id FROM searches s2
               WHERE s2.status != 'error' AND s2.searched_at >= ?
                 AND s2.searched_at = (
                     SELECT MAX(searched_at) FROM searches s3
                     WHERE s3.status != 'error'
                       AND s3.source = s2.source AND s3.group_id = s2.group_id
                       AND s3.direction = s2.direction AND s3.origins = s2.origins
                       AND s3.destinations = s2.destinations
                       AND s3.travel_date = s2.travel_date))""",
        (since.isoformat(),),
    ).fetchall()
    return [_offer(row) for row in rows]


def source_health(conn: sqlite3.Connection, run_id: int) -> dict[str, tuple[int, int]]:
    """Per source: (searches that worked, searches tried) in one run."""
    rows = conn.execute(
        """SELECT source, SUM(status != 'error') AS ok, COUNT(*) AS tried
           FROM searches WHERE run_id = ? GROUP BY source""",
        (run_id,),
    ).fetchall()
    return {row["source"]: (int(row["ok"]), int(row["tried"])) for row in rows}


def consecutive_failed_runs(conn: sqlite3.Connection, source: str) -> int:
    rows = conn.execute(
        """SELECT run_id, SUM(status != 'error') AS ok FROM searches
           WHERE source = ? AND run_id IS NOT NULL GROUP BY run_id ORDER BY run_id DESC LIMIT 10""",
        (source,),
    ).fetchall()
    failed = 0
    for row in rows:
        if int(row["ok"]) > 0:
            break
        failed += 1
    return failed


def last_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()
    return int(row["id"]) if row["id"] is not None else None


# Rates ----------------------------------------------------------------------


def save_rates(conn: sqlite3.Connection, rates: Rates) -> None:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO fx_rates (as_of, currency, per_eur) VALUES (?, ?, ?)",
            [(rates.as_of.isoformat(), code, str(rate)) for code, rate in rates.per_eur.items()],
        )


def latest_rates(conn: sqlite3.Connection) -> Rates | None:
    row = conn.execute("SELECT MAX(as_of) AS as_of FROM fx_rates").fetchone()
    if row["as_of"] is None:
        return None
    rows = conn.execute("SELECT currency, per_eur FROM fx_rates WHERE as_of = ?", (row["as_of"],))
    return Rates(
        per_eur={r["currency"]: Decimal(r["per_eur"]) for r in rows},
        as_of=date.fromisoformat(row["as_of"]),
    )


# Snapshots, alerts, summaries ---------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    window_id: str
    plan: str  # "main" or an alternative id
    entry: str
    exit: str
    total: Money
    score: Money
    taken_at: datetime


def save_snapshot(conn: sqlite3.Connection, run_id: int | None, snapshot: Snapshot) -> None:
    with conn:
        conn.execute(
            """INSERT INTO snapshots (run_id, window_id, plan, entry, exit, total_minor,
                   score_minor, taken_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                snapshot.window_id,
                snapshot.plan,
                snapshot.entry,
                snapshot.exit,
                snapshot.total.minor,
                snapshot.score.minor,
                snapshot.taken_at.isoformat(),
            ),
        )


def snapshots(conn: sqlite3.Connection, window_id: str, plan: str = "main") -> list[Snapshot]:
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE window_id = ? AND plan = ? ORDER BY taken_at",
        (window_id, plan),
    ).fetchall()
    return [
        Snapshot(
            window_id=row["window_id"],
            plan=row["plan"],
            entry=row["entry"],
            exit=row["exit"],
            total=Money(int(row["total_minor"]), "EUR"),
            score=Money(int(row["score_minor"]), "EUR"),
            taken_at=datetime.fromisoformat(row["taken_at"]),
        )
        for row in rows
    ]


def record_alert(
    conn: sqlite3.Connection, kind: str, window_id: str, total: Money, now: datetime
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO alerts (kind, window_id, total_minor, sent_at) VALUES (?, ?, ?, ?)",
            (kind, window_id, total.minor, now.isoformat()),
        )


def alerted_on(conn: sqlite3.Connection, window_id: str, day: date) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE window_id = ? AND substr(sent_at, 1, 10) = ?",
        (window_id, day.isoformat()),
    ).fetchone()
    return int(row["n"]) > 0


def record_summary(
    conn: sqlite3.Connection, week: str, totals: dict[str, Money], now: datetime
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO summaries (week, totals_json, sent_at) VALUES (?, ?, ?)",
            (week, json.dumps({k: v.minor for k, v in totals.items()}), now.isoformat()),
        )


def last_summary(conn: sqlite3.Connection) -> tuple[str, dict[str, Money]] | None:
    row = conn.execute("SELECT * FROM summaries ORDER BY sent_at DESC LIMIT 1").fetchone()
    if row is None:
        return None
    totals = {k: Money(int(v), "EUR") for k, v in json.loads(row["totals_json"]).items()}
    return row["week"], totals


# Todos and settings -------------------------------------------------------------


@dataclass(frozen=True)
class Todo:
    id: int
    text: str
    added_by: str
    done_by: str | None


def add_todo(conn: sqlite3.Connection, text: str, added_by: str, now: datetime) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO todos (text, added_by, added_at) VALUES (?, ?, ?)",
            (text, added_by, now.isoformat()),
        )
    return _row_id(cursor)


def todos(conn: sqlite3.Connection, include_done: bool = False) -> list[Todo]:
    query = "SELECT * FROM todos" + ("" if include_done else " WHERE done_at IS NULL")
    return [
        Todo(id=row["id"], text=row["text"], added_by=row["added_by"], done_by=row["done_by"])
        for row in conn.execute(query + " ORDER BY id")
    ]


def complete_todo(conn: sqlite3.Connection, todo_id: int, done_by: str, now: datetime) -> bool:
    with conn:
        cursor = conn.execute(
            "UPDATE todos SET done_by = ?, done_at = ? WHERE id = ? AND done_at IS NULL",
            (done_by, now.isoformat(), todo_id),
        )
    return cursor.rowcount == 1


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# Mapping helpers ----------------------------------------------------------------


def _row_id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("insert returned no row id")
    return cursor.lastrowid


def _key(row: sqlite3.Row) -> SearchKey:
    return SearchKey(
        source=row["source"],
        group_id=row["group_id"],
        direction=DIRECTIONS[row["direction"]],
        origins=tuple(row["origins"].split(",")),
        destinations=tuple(row["destinations"].split(",")),
        travel_date=date.fromisoformat(row["travel_date"]),
    )


def _segment_json(segment: Segment) -> dict[str, Any]:
    return {
        "origin": segment.origin,
        "destination": segment.destination,
        "depart": segment.depart.isoformat(),
        "arrive": segment.arrive.isoformat(),
        "minutes": segment.minutes,
        "airline_code": segment.airline_code,
        "airline_name": segment.airline_name,
        "flight_number": segment.flight_number,
        "logo_url": segment.logo_url,
        "stops_on_board": list(segment.stops_on_board),
    }


def _segment(raw: dict[str, Any]) -> Segment:
    return Segment(
        origin=raw["origin"],
        destination=raw["destination"],
        depart=datetime.fromisoformat(raw["depart"]),
        arrive=datetime.fromisoformat(raw["arrive"]),
        minutes=int(raw["minutes"]),
        airline_code=raw["airline_code"],
        airline_name=raw["airline_name"],
        flight_number=raw["flight_number"],
        logo_url=raw["logo_url"],
        stops_on_board=tuple(raw["stops_on_board"]),
    )


def _offer(row: sqlite3.Row) -> Offer:
    return Offer(
        source=row["source"],
        group_id=row["group_id"],
        direction=DIRECTIONS[row["direction"]],
        segments=tuple(_segment(s) for s in json.loads(row["segments_json"])),
        price=Money(int(row["price_minor"]), row["currency"]),
        total_minutes=int(row["total_minutes"]),
        self_transfer=bool(row["self_transfer"]),
        booking_url=row["booking_url"],
        found_on=datetime.fromisoformat(row["searched_at"]).date(),
    )
