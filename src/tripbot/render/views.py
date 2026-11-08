"""View models: plain data the card templates print. No HTML here."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from tripbot import i18n
from tripbot.domain.alerts import Alert
from tripbot.domain.flights import Offer, duration_label
from tripbot.domain.legs import Mode
from tripbot.domain.money import Money, Rates
from tripbot.domain.ranking import Combo, GroupChoice
from tripbot.domain.routes import PlannedLeg, Route
from tripbot.i18n import _, ngettext
from tripbot.pipeline.state import Rankings
from tripbot.render.names import Names, clock, initials

RAINIER_EUR_CENTS = 1_000  # a window is "rainier" at €10+ more weather cost per person
FLAGS = {"GBP": "gb", "BRL": "br", "MXN": "mx", "EUR": "eu", "USD": "us"}


@dataclass(frozen=True)
class MoneyLine:
    text: str
    flag: str


@dataclass(frozen=True)
class StopLine:
    """A connection; `wait` is empty for a same-plane stop."""

    airport: str
    wait: str
    detail: str


@dataclass(frozen=True)
class FlightHop:
    """One flight number's time in the air, placed between its two ends on the rail."""

    duration: str
    center: float  # percent along the rail, on the same scale as the stops


@dataclass(frozen=True)
class FlightLine:
    origin: str
    origin_city: str
    destination: str
    destination_city: str
    stops: tuple[StopLine, ...]
    hops: tuple[FlightHop, ...]
    day: str
    depart_time: str
    arrive_time: str
    arrive_shift: str
    duration: str
    note: str
    airline_code: str
    logo_url: str | None


@dataclass(frozen=True)
class GroupRow:
    name: str
    initials: tuple[str, ...]
    price: str
    price_note: str
    fairness_label: str
    fairness_width: int
    outbound: FlightLine
    inbound: FlightLine


@dataclass(frozen=True)
class TimelineItem:
    kind: str  # stop | leg
    name: str = ""
    endpoint: bool = False
    mode: str = ""
    duration: str = ""


@dataclass(frozen=True)
class DaySpan:
    kind: Literal["stay", "sleeper"]
    name: str
    nights: int
    nights_label: str
    arrive: str
    leave: str


@dataclass(frozen=True)
class AirportName:
    code: str
    name: str


@dataclass(frozen=True)
class LegendItem:
    mode: Mode
    label: str
    speed: str


@dataclass(frozen=True)
class WindowDot:
    id: str
    dates: str
    total: str
    delta: str | None
    delta_good: bool
    position: int  # 0 cheapest .. 100 dearest
    best: bool
    rainier: bool


@dataclass(frozen=True)
class Spark:
    lines: tuple[tuple[str, str], ...]  # (window id, svg points)
    low_x: float
    low_y: float
    low_label: str


@dataclass(frozen=True)
class SourceStatus:
    name: str
    ok: bool
    label: str


@dataclass(frozen=True)
class SummaryView:
    date_label: str
    total: str
    delta: str | None
    delta_good: bool
    includes: tuple[str, ...]
    per_person: tuple[MoneyLine, ...]
    window_dates: str
    window_days: str
    entry_code: str
    entry_city: str
    exit_code: str
    exit_city: str
    windows: tuple[WindowDot, ...]
    groups: tuple[GroupRow, ...]
    airports: tuple[AirportName, ...]
    timeline: tuple[TimelineItem, ...]
    legend: tuple[LegendItem, ...]
    days: tuple[DaySpan, ...]
    spark: Spark | None
    sources: tuple[SourceStatus, ...]


@dataclass(frozen=True)
class SummaryInputs:
    names: Names
    rankings: Rankings
    window_id: str
    reference_totals: Mapping[str, Money]
    history: Mapping[str, Sequence[Money]]
    health: Mapping[str, tuple[int, int]]
    today: date
    currencies: Sequence[str]


def build_summary(inputs: SummaryInputs) -> SummaryView | None:
    ranking = inputs.rankings.main[inputs.window_id]
    best = ranking.best
    if best is None:
        return None
    rates = inputs.rankings.rates
    names = inputs.names
    delta, good = _delta(best.total_eur, inputs.reference_totals.get(inputs.window_id))
    people = sum(choice.group.headcount for choice in best.choices)
    share = Money(best.total_eur.minor // people, "EUR")
    groups = tuple(group_row(choice, rates, names) for choice in best.choices)
    return SummaryView(
        date_label=i18n.day(inputs.today),
        total=i18n.money(best.total_eur),
        delta=delta,
        delta_good=good,
        includes=included(best.route, people),
        per_person=(
            MoneyLine(text=i18n.money(share), flag=FLAGS["EUR"]),
            *other_currencies(share, rates, inputs.currencies),
        ),
        window_dates=i18n.date_range(best.window.start, best.window.end),
        window_days=ngettext("{n} day", "{n} days", best.window.nights + 1).format(
            n=best.window.nights + 1
        ),
        entry_code=_airport_of(best, "entry"),
        entry_city=names.city(best.entry),
        exit_code=_airport_of(best, "exit"),
        exit_city=names.city(best.exit),
        windows=window_dots(inputs.rankings, inputs.reference_totals, inputs.window_id),
        groups=groups,
        airports=airport_names(groups, names),
        timeline=timeline(best.route, names),
        legend=legend(best.route, names),
        days=day_spans(best.route, names),
        spark=spark(inputs.history, inputs.window_id),
        sources=tuple(
            SourceStatus(name=_source_name(name), ok=worked > 0, label=f"{worked}/{tried}")
            for name, (worked, tried) in sorted(inputs.health.items())
        ),
    )


def included(route: Route, people: int) -> tuple[str, ...]:
    """What the group total pays for: every person's round trip and legs inside China."""
    flights = people * sum(1 for leg in route.legs if leg.option.mode == "flight")
    trains = people * sum(1 for leg in route.legs if leg.option.mode != "flight")
    items = [
        ngettext("{n} person", "{n} people", people).format(n=people),
        ngettext("{n} international ticket", "{n} international tickets", 2 * people).format(
            n=2 * people
        ),
    ]
    if flights:
        items.append(
            ngettext("{n} flight in China", "{n} flights in China", flights).format(n=flights)
        )
    if trains:
        items.append(ngettext("{n} train in China", "{n} trains in China", trains).format(n=trains))
    return tuple(items)


def airport_names(groups: Sequence[GroupRow], names: Names) -> tuple[AirportName, ...]:
    """Every airport code on the flight rows, in order of appearance, with its short name."""
    codes: list[str] = []
    for group in groups:
        for line in (group.outbound, group.inbound):
            codes += [line.origin, *(stop.airport for stop in line.stops), line.destination]
    return tuple(
        AirportName(code, name)
        for code in dict.fromkeys(codes)
        if (name := names.airport_name(code)) is not None
    )


def legend(route: Route, names: Names) -> tuple[LegendItem, ...]:
    """The modes this route uses, in a fixed order, with their average speed."""
    labels: dict[Mode, str] = {
        "train": _("high-speed"),
        "sleeper": _("sleeper"),
        "flight": _("flight"),
    }
    used = {leg.option.mode for leg in route.legs}
    speeds = names.reference.speeds_kmh
    return tuple(
        LegendItem(mode, label, f"~{speeds[mode]} km/h")
        for mode, label in labels.items()
        if mode in used
    )


def other_currencies(
    total: Money, rates: Rates, currencies: Sequence[str]
) -> tuple[MoneyLine, ...]:
    return tuple(
        MoneyLine(text=i18n.money(rates.from_eur(total, code)), flag=FLAGS.get(code, ""))
        for code in currencies
        if code != "EUR"
    )


def group_row(choice: GroupChoice, rates: Rates, names: Names) -> GroupRow:
    origin = names.config.origin_of(choice.group)
    local = rates.from_eur(choice.fare_eur, origin.currency)
    cap = names.config.fairness.max_price_overshoot_pct
    return GroupRow(
        name=choice.group.name,
        initials=tuple(initials(person) for person in choice.group.people),
        price=i18n.money(local),
        price_note=_("each") if choice.group.headcount > 1 else _("round trip"),
        fairness_label=f"+{choice.price_overshoot_pct:.0f}%",
        fairness_width=min(100, round(choice.price_overshoot_pct * 100 / cap)),
        outbound=flight_line(choice.outbound, names),
        inbound=flight_line(choice.inbound, names),
    )


def flight_line(offer: Offer, names: Names) -> FlightLine:
    first = offer.segments[0]
    shift = (offer.arrive.date() - offer.depart.date()).days
    return FlightLine(
        origin=offer.origin,
        origin_city=names.airport_city(offer.origin),
        destination=offer.destination,
        destination_city=names.airport_city(offer.destination),
        stops=(stops := stop_lines(offer)),
        hops=flight_hops(offer, stops),
        day=i18n.day(offer.depart.date()),
        depart_time=clock(offer.depart),
        arrive_time=clock(offer.arrive),
        arrive_shift=f"{shift:+d}" if shift else "",
        duration=duration_label(offer.total_minutes),
        note=_("via {airports}").format(airports=", ".join(offer.via))
        if offer.via
        else _("direct"),
        airline_code=first.airline_code,
        logo_url=first.logo_url,
    )


def stop_lines(offer: Offer) -> tuple[StopLine, ...]:
    """Stops in `offer.via` order: plane changes with the wait, same-plane stops without."""
    waits = [duration_label(layover.minutes) for layover in offer.layovers]
    stops: list[StopLine] = []
    for index, segment in enumerate(offer.segments):
        if index > 0:
            wait = waits[index - 1]
            stops.append(StopLine(segment.origin, wait, _("{wait} wait").format(wait=wait)))
        stops.extend(StopLine(airport, "", _("same plane")) for airport in segment.stops_on_board)
    return tuple(stops)


def flight_hops(offer: Offer, stops: tuple[StopLine, ...]) -> tuple[FlightHop, ...]:
    """Each flight's own duration when there's a connection; a direct flight has only its total.

    Same-plane stops don't split a flight, so its duration spans them.
    """
    if len(offer.segments) < 2:
        return ()
    slots = len(stops) + 1
    changes = (index * 100 / slots for index, stop in enumerate(stops, start=1) if stop.wait)
    edges = [0.0, *changes, 100.0]
    return tuple(
        FlightHop(duration_label(segment.minutes), round((left + right) / 2, 1))
        for segment, (left, right) in zip(offer.segments, pairwise(edges), strict=True)
    )


def timeline(route: Route, names: Names) -> tuple[TimelineItem, ...]:
    items: list[TimelineItem] = []
    if route.entry != route.order[0]:
        items.append(TimelineItem(kind="stop", name=names.city(route.entry), endpoint=True))
    legs = list(route.legs)
    if legs and route.entry != route.order[0]:
        items.append(_leg_item(legs.pop(0)))
    for index, stay in enumerate(route.stays):
        is_first = index == 0 and route.entry == stay.city
        is_last = index == len(route.stays) - 1 and route.exit == stay.city
        items.append(
            TimelineItem(kind="stop", name=names.city(stay.city), endpoint=is_first or is_last)
        )
        if legs:
            items.append(_leg_item(legs.pop(0)))
    if route.exit != route.order[-1]:
        items.append(TimelineItem(kind="stop", name=names.city(route.exit), endpoint=True))
    return tuple(items)


def _leg_item(leg: PlannedLeg) -> TimelineItem:
    option = leg.option
    return TimelineItem(kind="leg", mode=option.mode, duration=duration_label(option.minutes))


def day_spans(route: Route, names: Names) -> tuple[DaySpan, ...]:
    """Every night of the window in date order: hotel stays and sleeper trains."""
    spans = [
        (stay.arrive, _span("stay", names.city(stay.city), stay.arrive, stay.nights))
        for stay in route.stays
    ] + [
        (leg.day, _span("sleeper", _("Sleeper"), leg.day, 1))
        for leg in route.legs
        if leg.option.overnight
    ]
    return tuple(span for _, span in sorted(spans, key=lambda dated: dated[0]))


def _span(kind: Literal["stay", "sleeper"], name: str, first: date, nights: int) -> DaySpan:
    leave = first + timedelta(days=nights)
    return DaySpan(
        kind,
        name,
        nights,
        ngettext("{n} night", "{n} nights", nights).format(n=nights),
        i18n.numeric_day(first),
        i18n.numeric_day(leave),
    )


def window_dots(
    rankings: Rankings, reference_totals: Mapping[str, Money], selected: str
) -> tuple[WindowDot, ...]:
    totals_minor = [r.best.total_eur.minor for r in rankings.main.values() if r.best is not None]
    driest = min(
        (r.best.route.weather_eur.minor for r in rankings.main.values() if r.best is not None),
        default=0,
    )
    dots = []
    for window_id, ranking in rankings.main.items():
        if ranking.best is None:
            continue
        total = ranking.best.total_eur
        delta, good = _delta(total, reference_totals.get(window_id))
        dots.append(
            WindowDot(
                id=window_id,
                dates=i18n.date_range(ranking.window.start, ranking.window.end),
                total=i18n.money(total),
                delta=delta,
                delta_good=good,
                position=_position(total.minor, totals_minor),
                best=window_id == selected,
                rainier=ranking.best.route.weather_eur.minor - driest >= RAINIER_EUR_CENTS,
            )
        )
    return tuple(dots)


def spark(history: Mapping[str, Sequence[Money]], selected: str) -> Spark | None:
    series = {w: [m.minor for m in totals] for w, totals in history.items() if len(totals) >= 2}
    if selected not in series:
        return None
    values = [v for points in series.values() for v in points]
    low, high = min(values), max(values)
    span = max(1, high - low)

    def points(totals: list[int]) -> list[tuple[float, float]]:
        step = 440 / max(1, len(totals) - 1)
        return [(20 + i * step, 10 + (high - v) * 140 / span) for i, v in enumerate(totals)]

    lines = tuple(
        (w, " ".join(f"{x:.1f},{y:.1f}" for x, y in points(totals))) for w, totals in series.items()
    )
    chosen = series[selected]
    low_index = chosen.index(min(chosen))
    low_x, low_y = points(chosen)[low_index]
    return Spark(
        lines=lines,
        low_x=low_x,
        low_y=low_y,
        low_label=Money(min(chosen), "EUR").whole(),
    )


def _position(value: int, values: list[int]) -> int:
    low, high = min(values), max(values)
    return 50 if high == low else round((value - low) * 100 / (high - low))


def _delta(total: Money, reference: Money | None) -> tuple[str | None, bool]:
    if reference is None or reference == total:
        return None, False
    change = total - reference
    good = change.minor < 0
    return i18n.money(Money(abs(change.minor), "EUR")), good


def _airport_of(combo: Combo, side: str) -> str:
    offer = combo.choices[0].outbound if side == "entry" else combo.choices[0].inbound
    return offer.destination if side == "entry" else offer.origin


def _source_name(name: str) -> str:
    return {"google": "Google Flights", "serpapi": "SerpApi", "tripcom": "Trip.com"}.get(name, name)


# Alert card ---------------------------------------------------------------------


@dataclass(frozen=True)
class AlertRow:
    name: str
    origin: str
    price: str


@dataclass(frozen=True)
class AlertView:
    headline: str
    record: bool
    total: str
    saving: str
    saving_pct: str
    others: tuple[MoneyLine, ...]
    window_id: str
    window_dates: str
    entry_code: str
    exit_code: str
    rows: tuple[AlertRow, ...]
    previous: str
    date_label: str


def _alert_headline(alert: Alert) -> str:
    if alert.kind == "record_low":
        return _("New record low")
    if alert.kind == "target":
        return _("Below your target")
    return _("Down {pct}% this week").format(pct=f"{alert.saving_pct:.0f}")


def _alert_previous(alert: Alert) -> str:
    previous = i18n.money(alert.previous)
    if alert.kind == "record_low":
        return _("Previous low {total}").format(total=previous)
    if alert.kind == "target":
        return _("Target {total}").format(total=previous)
    return _("Last summary {total}").format(total=previous)


def build_alert(
    alert: Alert, combo: Combo, rates: Rates, names: Names, currencies: Sequence[str], today: date
) -> AlertView:
    record = alert.kind == "record_low"
    return AlertView(
        headline=_alert_headline(alert),
        record=record,
        total=i18n.money(alert.total),
        saving=i18n.money(alert.saving),
        saving_pct=f"−{alert.saving_pct:.1f}%",
        others=other_currencies(alert.total, rates, currencies),
        window_id=alert.window_id,
        window_dates=i18n.date_range(combo.window.start, combo.window.end),
        entry_code=_airport_of(combo, "entry"),
        exit_code=_airport_of(combo, "exit"),
        rows=tuple(
            AlertRow(
                name=choice.group.name,
                origin=choice.group.origin,
                price=i18n.money(
                    rates.from_eur(choice.fare_eur, names.config.origin_of(choice.group).currency)
                ),
            )
            for choice in combo.choices
        ),
        previous=_alert_previous(alert),
        date_label=i18n.day(today),
    )
