"""The nightly run: refresh rates, search, rank, snapshot, alert, report health."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial

from tripbot.config import TripConfig
from tripbot.domain.alerts import Alert, decide_alerts
from tripbot.domain.money import Money, Rates
from tripbot.domain.searches import SourcePlan, plan_tonight
from tripbot.pipeline.state import MAIN, Rankings, focus_of, load_rankings, search_space
from tripbot.reference import Reference
from tripbot.sources.base import FlightSource, timed
from tripbot.store import repo

FAILED_RUNS_BEFORE_NOTICE = 2
TARGET_KEY = "target_eur"


@dataclass
class NightlyDeps:
    config: TripConfig
    reference: Reference
    conn: sqlite3.Connection
    sources: Sequence[FlightSource]
    fetch_rates: Callable[[], Rates] | None = None
    log: Callable[[str], None] = print


@dataclass
class NightlyReport:
    run_id: int
    health: dict[str, tuple[int, int]]
    failing_sources: list[str]
    newly_failing: list[str]
    alerts: list[Alert]
    rankings: Rankings
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return any(worked for worked, _ in self.health.values()) or not self.health


def run_nightly(deps: NightlyDeps, now: datetime) -> NightlyReport:
    run_id = repo.start_run(deps.conn, now)
    notes = _refresh_rates(deps)

    before = load_rankings(deps.conn, deps.config, deps.reference, now)
    space = search_space(deps.config, deps.reference)
    last = repo.last_searched(deps.conn)
    for source in deps.sources:
        plan = SourcePlan(
            name=source.name,
            many_airports=source.shape.many_airports,
            city_codes=source.shape.city_codes,
            budget=_budget(deps.config, source.name),
        )
        keys = plan_tonight(plan, space, last, focus_of(before))
        deps.log(f"{source.name}: {len(keys)} searches")
        for key in keys:
            result = timed(partial(source.search, key))
            repo.record_search(deps.conn, run_id, key, result, now)

    rankings = load_rankings(deps.conn, deps.config, deps.reference, now)
    plan_keys = deps.config.plan.keys()
    alerts = _alerts(deps, rankings, plan_keys, now)
    _snapshot(deps.conn, run_id, rankings, plan_keys, now)

    health = repo.source_health(deps.conn, run_id)
    failed_runs = {
        source.name: repo.consecutive_failed_runs(deps.conn, source.name) for source in deps.sources
    }
    failing = [name for name, runs in failed_runs.items() if runs >= FAILED_RUNS_BEFORE_NOTICE]
    newly = [name for name, runs in failed_runs.items() if runs == FAILED_RUNS_BEFORE_NOTICE]
    report = NightlyReport(run_id, health, failing, newly, alerts, rankings, notes)
    repo.finish_run(deps.conn, run_id, "ok" if report.ok else "failed", now)
    return report


def _budget(config: TripConfig, source: str) -> int:
    budgets = {
        "google": config.budgets.google,
        "serpapi": config.budgets.serpapi,
        "tripcom": config.budgets.tripcom,
    }
    return budgets.get(source, 0)


def _refresh_rates(deps: NightlyDeps) -> list[str]:
    if deps.fetch_rates is None:
        return []
    try:
        repo.save_rates(deps.conn, deps.fetch_rates())
    except Exception as error:  # the feed is optional; yesterday's rates still work
        return [f"rates not refreshed: {type(error).__name__}"]
    return []


def _alerts(
    deps: NightlyDeps, rankings: Rankings, keys: dict[str, str], now: datetime
) -> list[Alert]:
    totals: dict[str, Money] = {}
    history: dict[str, list[Money]] = {}
    alerted: set[str] = set()
    for window_id, ranking in rankings.main.items():
        if ranking.best is not None:
            totals[window_id] = ranking.best.total_eur
        history[window_id] = [
            s.total for s in repo.snapshots(deps.conn, window_id, keys[window_id], MAIN)
        ]
        if repo.alerted_on(deps.conn, window_id, now.date()):
            alerted.add(window_id)
    summary = repo.last_summary(deps.conn)
    target = repo.get_setting(deps.conn, TARGET_KEY)
    alerts = decide_alerts(
        totals,
        history,
        summary.comparable(keys) if summary else None,
        deps.config.alerts,
        alerted,
        Money.of(target, "EUR") if target else None,
    )
    for alert in alerts:
        repo.record_alert(deps.conn, alert.kind, alert.window_id, alert.total, now)
    return alerts


def _snapshot(
    conn: sqlite3.Connection,
    run_id: int,
    rankings: Rankings,
    keys: dict[str, str],
    now: datetime,
) -> None:
    plans = [(MAIN, rankings.main)]
    plans += [
        (alt_id, {w: by_alt[alt_id] for w, by_alt in rankings.alternatives.items()})
        for alt_id in _alternative_ids(rankings)
    ]
    for plan, by_window in plans:
        for window_id, ranking in by_window.items():
            if ranking.best is None:
                continue
            repo.save_snapshot(
                conn,
                run_id,
                repo.Snapshot(
                    window_id=window_id,
                    plan=plan,
                    plan_key=keys[window_id],
                    entry=ranking.best.entry,
                    exit=ranking.best.exit,
                    total=ranking.best.total_eur,
                    score=ranking.best.score_eur,
                    taken_at=now,
                ),
            )


def _alternative_ids(rankings: Rankings) -> list[str]:
    ids: list[str] = []
    for by_alt in rankings.alternatives.values():
        for alt_id in by_alt:
            if alt_id not in ids:
                ids.append(alt_id)
    return ids
