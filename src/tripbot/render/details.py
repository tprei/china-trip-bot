"""View models for the on-demand cards: route, map, history and comparisons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from tripbot import i18n
from tripbot.domain.flights import duration_label
from tripbot.domain.legs import Mode
from tripbot.domain.money import Money
from tripbot.domain.ranking import Combo, WindowRanking
from tripbot.domain.routes import PlannedLeg
from tripbot.i18n import _, ngettext
from tripbot.render.maps import china
from tripbot.render.names import Names
from tripbot.render.views import RAINIER_EUR_CENTS, LegendItem, legend

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


@dataclass(frozen=True)
class MapStop:
    """A city on the map. `order` counts stays; an entry or exit city may have no stay."""

    name: str
    x: float
    y: float
    order: int | None
    nights: str
    dates: str
    arrival: bool
    departure: bool


@dataclass(frozen=True)
class MapLeg:
    mode: Mode
    x1: float
    y1: float
    x2: float
    y2: float
    duration: str


@dataclass(frozen=True)
class MapView:
    window_id: str
    dates: str
    days: str
    width: int
    height: int
    outline: str
    stops: tuple[MapStop, ...]
    legs: tuple[MapLeg, ...]
    legend: tuple[LegendItem, ...]


def build_map(combo: Combo, names: Names) -> MapView:
    """The route drawn on China: one point per city, a line per leg, in travel order."""
    route = combo.route
    outline = china()

    def point(city_id: str) -> tuple[float, float]:
        city = names.reference.cities[city_id]
        return outline.point(city.latitude, city.longitude)

    stays = {stay.city: (index, stay) for index, stay in enumerate(route.stays, start=1)}
    cities = list(dict.fromkeys([route.entry, *route.order, route.exit]))
    stops = []
    for city_id in cities:
        x, y = point(city_id)
        order, stay = stays.get(city_id, (None, None))
        nights = ""
        dates = ""
        if stay is not None:
            nights = ngettext("{n} night", "{n} nights", stay.nights).format(n=stay.nights)
            leave = stay.arrive + timedelta(days=stay.nights)
            dates = f"{i18n.numeric_day(stay.arrive)}–{i18n.numeric_day(leave)}"
        stops.append(
            MapStop(
                name=names.city(city_id),
                x=x,
                y=y,
                order=order,
                nights=nights,
                dates=dates,
                arrival=city_id == route.entry,
                departure=city_id == route.exit,
            )
        )
    legs = tuple(
        MapLeg(
            leg.option.mode,
            *point(leg.origin),
            *point(leg.destination),
            duration_label(leg.option.minutes),
        )
        for leg in route.legs
    )
    days = combo.window.nights + 1
    return MapView(
        window_id=combo.window.id,
        dates=i18n.date_range(combo.window.start, combo.window.end),
        days=ngettext("{n} day", "{n} days", days).format(n=days),
        width=outline.width,
        height=outline.height,
        outline=outline.path,
        stops=tuple(stops),
        legs=legs,
        legend=legend(route, names),
    )


def build_route(combo: Combo, names: Names, plan_label: str) -> RouteView:
    steps: list[RouteStep] = []
    legs = list(combo.route.legs)
    stays = list(combo.route.stays)
    if combo.route.entry != combo.route.order[0]:
        steps.append(_landing(combo, names))
        steps.append(_leg_step(legs.pop(0), names))
    for index, stay in enumerate(stays):
        steps.append(
            RouteStep(
                kind="stay",
                day=i18n.day(stay.arrive),
                title=names.city(stay.city),
                detail=ngettext("{n} night", "{n} nights", stay.nights).format(n=stay.nights),
            )
        )
        if index < len(stays) - 1 or combo.route.exit != stay.city:
            steps.append(_leg_step(legs.pop(0), names))
    return RouteView(
        window_id=combo.window.id,
        plan_label=plan_label,
        dates=i18n.date_range(combo.window.start, combo.window.end),
        steps=tuple(steps),
        internal=i18n.money(combo.route.fare_eur),
        sleepers=combo.route.sleepers,
    )


def _landing(combo: Combo, names: Names) -> RouteStep:
    return RouteStep(
        kind="stay",
        day=i18n.day(combo.window.start),
        title=names.city(combo.route.entry),
        detail=_("land"),
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
    parts.append(f"≈ {i18n.money(leg.fare_eur)}")
    return RouteStep(
        kind="leg",
        day=i18n.day(leg.day),
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
            latest=i18n.money(points[-1][1]),
            low=i18n.money(min((v for _, v in points), key=lambda money: money.minor)),
            selected=window_id == selected,
        )
        for window_id, points in series.items()
    )
    return HistoryView(
        lines=lines,
        y_labels=(
            (y(high), i18n.money(Money(high, "EUR"))),
            (y(low), i18n.money(Money(low, "EUR"))),
        ),
        x_labels=(
            (x(start), i18n.short_day(start.date())),
            (x(end), i18n.short_day(end.date())),
        ),
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
        label = _("Window {window}").format(window=window_id)
        if best is None:
            rows.append(CompareRow(label, _("no complete combo"), "—", None, False, 0, False))
            continue
        rainier = best.route.weather_eur.minor - driest >= RAINIER_EUR_CENTS
        dates = i18n.date_range(best.window.start, best.window.end)
        sub = _("{dates} · rainier").format(dates=dates) if rainier else dates
        row = _row(label, sub, best, cheapest, totals)
        rows.append(replace(row, highlight=best is pick))
    return CompareView(kicker=_("Windows"), title=_("Best plan per window"), rows=tuple(rows))


def compare_alternatives(
    main: WindowRanking, alternatives: Mapping[str, tuple[str, WindowRanking]], names: Names
) -> CompareView:
    entries: list[tuple[str, Combo | None]] = [(_("Main plan"), main.best)]
    entries += [(label, ranking.best) for label, ranking in alternatives.values()]
    totals = [best.total_eur.minor for _, best in entries if best is not None]
    base = main.best.total_eur.minor if main.best is not None else (min(totals) if totals else 0)
    rows = []
    for index, (label, best) in enumerate(entries):
        if best is None:
            rows.append(CompareRow(label, _("no complete combo"), "—", None, False, 0, False))
            continue
        sub = " → ".join(names.city(city) for city in best.route.order)
        if main.best is not None and best.window != main.best.window:
            sub += " · " + i18n.date_range(best.window.start, best.window.end)
        row = _row(label, sub, best, base, totals)
        rows.append(row if index else replace(row, delta=None, highlight=True))
    return CompareView(
        kicker=_("Other plans · window {window}").format(window=main.window.id),
        title=_("Main plan vs. dropping a city"),
        rows=tuple(rows),
    )


def _row(label: str, sub: str, best: Combo, base: int, totals: list[int]) -> CompareRow:
    change = best.total_eur.minor - base
    top = max(totals) if totals else 1
    return CompareRow(
        label=label,
        sub=sub,
        total=i18n.money(best.total_eur),
        delta=None if change == 0 else i18n.money(Money(abs(change), "EUR")),
        delta_good=change < 0,
        width=round(best.total_eur.minor * 100 / max(1, top)),
        highlight=change == 0,
    )
