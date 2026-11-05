"""View models for the on-demand cards: route, history and comparisons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from tripbot.domain.flights import duration_label
from tripbot.domain.money import Money
from tripbot.domain.ranking import Combo, WindowRanking
from tripbot.domain.routes import PlannedLeg
from tripbot.render.names import Names, day_label, short_date
from tripbot.render.views import RAINIER_EUR_CENTS

CHART_LEFT, CHART_RIGHT, CHART_TOP, CHART_BOTTOM = 70, 900, 20, 330


@dataclass(frozen=True)
class RouteStep:
    kind: str  # stay | leg
    day: str
    title: str
    detail: str
    mode: str = ""
    estimate: bool = False


@dataclass(frozen=True)
class RouteView:
    window_id: str
    plan_label: str
    dates: str
    steps: tuple[RouteStep, ...]
    internal: str
    sleepers: int


def build_route(combo: Combo, names: Names, plan_label: str) -> RouteView:
    steps: list[RouteStep] = []
    legs = list(combo.route.legs)
    stays = list(combo.route.stays)
    if combo.route.entry != combo.route.order[0]:
        steps.append(_landing(combo, names))
        steps.append(_leg_step(legs.pop(0), names))
    for index, stay in enumerate(stays):
        nights = "night" if stay.nights == 1 else "nights"
        steps.append(
            RouteStep(
                kind="stay",
                day=day_label(stay.arrive),
                title=names.city(stay.city),
                detail=f"{stay.nights} {nights}",
            )
        )
        if index < len(stays) - 1 or combo.route.exit != stay.city:
            steps.append(_leg_step(legs.pop(0), names))
    return RouteView(
        window_id=combo.window.id,
        plan_label=plan_label,
        dates=combo.window.label(),
        steps=tuple(steps),
        internal=combo.route.fare_eur.whole(),
        sleepers=combo.route.sleepers,
    )


def _landing(combo: Combo, names: Names) -> RouteStep:
    return RouteStep(
        kind="stay",
        day=day_label(combo.window.start),
        title=names.city(combo.route.entry),
        detail="land",
    )


def _leg_step(leg: PlannedLeg, names: Names) -> RouteStep:
    option = leg.option
    times = (
        f"{option.depart} → {option.arrive}"
        if option.depart and option.arrive
        else (duration_label(option.minutes))
    )
    parts = [times]
    if option.cabin:
        parts.append(option.cabin)
    parts.append(f"≈ {leg.fare_eur.whole()}")
    return RouteStep(
        kind="leg",
        day=day_label(leg.day),
        title=f"{names.city(leg.origin)} → {names.city(leg.destination)} · {option.name}",
        detail=" · ".join(parts),
        mode=option.mode,
        estimate=option.estimate,
    )


@dataclass(frozen=True)
class HistoryLine:
    window_id: str
    points: str
    latest: str
    low: str
    selected: bool


@dataclass(frozen=True)
class HistoryView:
    lines: tuple[HistoryLine, ...]
    y_labels: tuple[tuple[float, str], ...]
    x_labels: tuple[tuple[float, str], ...]


def build_history(
    history: Mapping[str, Sequence[tuple[datetime, Money]]], selected: str
) -> HistoryView | None:
    series = {w: list(points) for w, points in history.items() if len(points) >= 2}
    if not series:
        return None
    moments = [moment for points in series.values() for moment, _ in points]
    values = [money.minor for points in series.values() for _, money in points]
    start, end = min(moments), max(moments)
    low, high = min(values), max(values)
    span_s = max(1.0, (end - start).total_seconds())
    span_v = max(1, high - low)

    def x(moment: datetime) -> float:
        return CHART_LEFT + (moment - start).total_seconds() * (CHART_RIGHT - CHART_LEFT) / span_s

    def y(value: int) -> float:
        return CHART_TOP + (high - value) * (CHART_BOTTOM - CHART_TOP) / span_v

    lines = tuple(
        HistoryLine(
            window_id=window_id,
            points=" ".join(f"{x(m):.1f},{y(v.minor):.1f}" for m, v in points),
            latest=points[-1][1].whole(),
            low=min((v for _, v in points), key=lambda money: money.minor).whole(),
            selected=window_id == selected,
        )
        for window_id, points in series.items()
    )
    return HistoryView(
        lines=lines,
        y_labels=((y(high), Money(high, "EUR").whole()), (y(low), Money(low, "EUR").whole())),
        x_labels=((x(start), short_date(start.date())), (x(end), short_date(end.date()))),
    )


@dataclass(frozen=True)
class CompareRow:
    label: str
    sub: str
    total: str
    delta: str | None
    delta_good: bool
    width: int
    highlight: bool


@dataclass(frozen=True)
class CompareView:
    kicker: str
    title: str
    rows: tuple[CompareRow, ...]


def compare_windows(rankings: Mapping[str, WindowRanking]) -> CompareView:
    """Same pick as the summary: best score, so weather counts, not just price."""
    entries = [(w, r.best) for w, r in rankings.items()]
    bests = [best for _, best in entries if best is not None]
    totals = [best.total_eur.minor for best in bests]
    cheapest = min(totals) if totals else 0
    pick = min(bests, key=lambda best: best.score_eur.minor, default=None)
    driest = min((best.route.weather_eur.minor for best in bests), default=0)
    rows = []
    for window_id, best in entries:
        if best is None:
            rows.append(
                CompareRow(f"Window {window_id}", "no complete combo", "—", None, False, 0, False)
            )
            continue
        rainier = best.route.weather_eur.minor - driest >= RAINIER_EUR_CENTS
        sub = best.window.label() + (" · rainier" if rainier else "")
        row = _row(f"Window {window_id}", sub, best, cheapest, totals)
        rows.append(replace(row, highlight=best is pick))
    return CompareView(kicker="Windows", title="Best plan per window", rows=tuple(rows))


def compare_alternatives(
    main: WindowRanking, alternatives: Mapping[str, tuple[str, WindowRanking]], names: Names
) -> CompareView:
    entries: list[tuple[str, Combo | None]] = [("All four cities", main.best)]
    entries += [(label, ranking.best) for label, ranking in alternatives.values()]
    totals = [best.total_eur.minor for _, best in entries if best is not None]
    base = main.best.total_eur.minor if main.best is not None else (min(totals) if totals else 0)
    rows = []
    for index, (label, best) in enumerate(entries):
        if best is None:
            rows.append(CompareRow(label, "no complete combo", "—", None, False, 0, False))
            continue
        order = " → ".join(names.city(city) for city in best.route.order)
        row = _row(label, order, best, base, totals)
        rows.append(row if index else replace(row, delta=None, highlight=True))
    return CompareView(
        kicker=f"Other plans · window {main.window.id}",
        title="Main plan vs. dropping a city",
        rows=tuple(rows),
    )


def _row(label: str, sub: str, best: Combo, base: int, totals: list[int]) -> CompareRow:
    change = best.total_eur.minor - base
    top = max(totals) if totals else 1
    return CompareRow(
        label=label,
        sub=sub,
        total=best.total_eur.whole(),
        delta=None if change == 0 else Money(abs(change), "EUR").whole(),
        delta_good=change < 0,
        width=round(best.total_eur.minor * 100 / max(1, top)),
        highlight=change == 0,
    )
