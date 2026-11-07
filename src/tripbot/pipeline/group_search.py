"""Explicit bounded group run; no publication, notifications or health pings."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tripbot.config import TripConfig
from tripbot.domain.roundtrip import (
    RoundTripQuote,
    RoundTripRequest,
    rank_group_quotes,
    rank_roundtrips,
)
from tripbot.domain.searches import SearchKey
from tripbot.domain.trip import Window
from tripbot.reference import Reference, ranking_inputs
from tripbot.sources.base import FlightSource
from tripbot.sources.tripcom_fallback import TripComFallback


@dataclass(frozen=True)
class GroupRunPlan:
    window: Window
    requests: tuple[RoundTripRequest, ...]


def validate_plan(config: TripConfig, plan: GroupRunPlan) -> None:
    groups = {group.id: group for group in config.groups}
    if plan.window not in config.windows:
        raise ValueError("window not in config")
    if {request.group_id for request in plan.requests} != set(groups):
        raise ValueError("plan must cover all configured groups")
    for request in plan.requests:
        group = groups[request.group_id]
        if request.adults != group.headcount:
            raise ValueError("passenger count differs from config")
        if request.origin not in config.origins[group.origin].airports:
            raise ValueError("origin differs from config")
        if request.destination not in config.china_airports:
            raise ValueError("destination outside configured China airports")
        if request.outbound_date not in plan.window.outbound_dates(config.flex):
            raise ValueError("outbound outside configured flexibility")
        if request.return_date not in plan.window.return_dates(config.flex):
            raise ValueError("return outside configured flexibility")


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def run_groups(
    config: TripConfig,
    reference: Reference,
    plan: GroupRunPlan,
    tripcom: TripComFallback,
    google: FlightSource,
    output: Path,
) -> Path:
    """Store complete raw results separately from one-way inventory.

    Google retains its existing one-way/person semantics; it is comparison
    evidence, not a validated group joint fare. Only joint fares enter combos.
    """
    validate_plan(config, plan)
    output.mkdir(parents=True, exist_ok=True)
    db_path = output / "group-search.sqlite3"
    if db_path.exists():
        raise ValueError("use a fresh output directory; never overwrite a previous run")
    quotes: list[RoundTripQuote] = []
    records: list[dict[str, object]] = []
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE results (group_id TEXT, source TEXT, payload TEXT)")
        for request in plan.requests:
            trip_result = tripcom.search(request)
            quotes.extend(trip_result.quotes)
            payload = asdict(trip_result)
            records.append({"request": asdict(request), "tripcom": payload})
            _save(conn, request.group_id, "tripcom", payload)
            if tripcom.last_api_result is not None:
                records.append({"tripcom_api_one_way_per_person": asdict(tripcom.last_api_result)})
            for direction in ("outbound", "return"):
                key = _key(request, direction == "outbound")
                time.sleep(4)
                result = google.search(key)
                google_payload = {"key": asdict(key), "result": asdict(result)}
                _save(conn, request.group_id, "google", google_payload)
                records.append({"google_one_way_per_person": google_payload})
            conn.commit()
        ranking = rank_roundtrips(
            plan.window,
            quotes,
            config.route.stops,
            ranking_inputs(config, reference, config.fallback_rates),
            config.flights,
        )
        report = {
            "captured_at": datetime.now(UTC),
            "plan": asdict(plan),
            "results": records,
            "joint_ranking": asdict(ranking),
            "per_group": {
                group.id: [
                    asdict(q)
                    for q in rank_group_quotes(
                        plan.window,
                        quotes,
                        group,
                        ranking_inputs(config, reference, config.fallback_rates),
                        config.flights,
                    )
                ]
                for group in config.groups
            },
            "limits": [
                "Bounded sample, not market-wide best",
                "Internal route amounts are dated estimates, not live quotes",
                "Google offers are one-way/person, not group inventory validation",
                "No checkout, held inventory or purchasable package",
            ],
        }
        path = output / "results.json"
        path.write_text(json.dumps(report, default=_json_default, indent=2), encoding="utf-8")
    return path


def _save(conn: sqlite3.Connection, group: str, source: str, payload: object) -> None:
    conn.execute(
        "INSERT INTO results VALUES (?, ?, ?)",
        (group, source, json.dumps(payload, default=_json_default)),
    )


def _key(request: RoundTripRequest, outbound: bool) -> SearchKey:
    return SearchKey(
        "google",
        request.group_id,
        "outbound" if outbound else "return",
        (request.origin if outbound else request.destination,),
        (request.destination if outbound else request.origin,),
        request.outbound_date if outbound else request.return_date,
    )


def load_plan(path: Path, config: TripConfig, window_id: str) -> GroupRunPlan:
    """JSON list with explicit group, airports and ISO dates; pax comes from config."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    groups = {group.id: group for group in config.groups}
    window = next(window for window in config.windows if window.id == window_id)
    requests = tuple(
        RoundTripRequest(
            item["group_id"],
            item["origin"],
            item["destination"],
            date.fromisoformat(item["outbound_date"]),
            date.fromisoformat(item["return_date"]),
            groups[item["group_id"]].headcount,
        )
        for item in raw
    )
    plan = GroupRunPlan(window, requests)
    validate_plan(config, plan)
    return plan
