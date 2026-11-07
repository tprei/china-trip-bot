"""Observed joint fares: journeys are never independently priced."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise, product
from typing import Literal

from tripbot.config import FlightRules
from tripbot.domain.flights import Layover, Segment, duration_label
from tripbot.domain.money import Money
from tripbot.domain.ranking import RankingInputs
from tripbot.domain.routes import Route, plan_route
from tripbot.domain.trip import Group, Window


@dataclass(frozen=True)
class RoundTripRequest:
    group_id: str
    origin: str
    destination: str
    outbound_date: date
    return_date: date
    adults: int

    def __post_init__(self) -> None:
        if type(self.adults) is not int or self.adults < 1:
            raise ValueError("adults must be a positive integer")
        if not self.group_id or not self.origin or not self.destination:
            raise ValueError("group and airports are required")
        if self.origin == self.destination or self.return_date <= self.outbound_date:
            raise ValueError("invalid round-trip endpoints or dates")


@dataclass(frozen=True)
class Journey:
    segments: tuple[Segment, ...]
    total_minutes: int
    self_transfer: bool = False

    def __post_init__(self) -> None:
        if not self.segments or self.total_minutes <= 0:
            raise ValueError("journey needs segments and positive duration")
        if any(s.minutes <= 0 for s in self.segments):
            raise ValueError("segment duration must be positive")
        if self.total_minutes < sum(s.minutes for s in self.segments):
            raise ValueError("journey shorter than its flights")
        for before, after in pairwise(self.segments):
            if before.destination != after.origin or after.depart < before.arrive:
                raise ValueError("disconnected or reversed connection")

    @property
    def origin(self) -> str:
        return self.segments[0].origin

    @property
    def destination(self) -> str:
        return self.segments[-1].destination

    @property
    def depart(self) -> datetime:
        return self.segments[0].depart

    @property
    def arrive(self) -> datetime:
        return self.segments[-1].arrive

    @property
    def stops(self) -> int:
        return len(self.segments) - 1 + sum(len(s.stops_on_board) for s in self.segments)

    @property
    def layovers(self) -> list[Layover]:
        return [
            Layover(a.destination, int((b.depart - a.arrive).total_seconds() // 60))
            for a, b in pairwise(self.segments)
        ]

    @property
    def via(self) -> tuple[str, ...]:
        airports: list[str] = []
        for index, segment in enumerate(self.segments):
            if index:
                airports.append(segment.origin)
            airports.extend(segment.stops_on_board)
        return tuple(airports)

    @property
    def airlines(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.airline_name for s in self.segments))

    def summary(self) -> str:
        path = " → ".join((self.origin, *self.via, self.destination))
        return f"{path} · {duration_label(self.total_minutes)} · {' + '.join(self.airlines)}"


@dataclass(frozen=True)
class RoundTripQuote:
    request: RoundTripRequest
    outbound: Journey
    inbound: Journey
    total: Money
    per_person: Money
    quoted_at: datetime
    source: str
    booking_url: str | None
    fare_notes: str

    def __post_init__(self) -> None:
        expected = (self.request.origin, self.request.destination)
        if (self.outbound.origin, self.outbound.destination) != expected:
            raise ValueError("outbound does not match request")
        if (self.inbound.destination, self.inbound.origin) != expected:
            raise ValueError("inbound does not match request")
        if (self.outbound.depart.date(), self.inbound.depart.date()) != (
            self.request.outbound_date,
            self.request.return_date,
        ):
            raise ValueError("journey dates do not match request")
        if self.outbound.arrive > self.inbound.depart:
            raise ValueError("return precedes arrival")
        for price in (self.total, self.per_person):
            if type(price.minor) is not int or price.minor <= 0:
                raise ValueError("observed prices must be positive integer minor units")
            if len(price.currency) != 3 or not price.currency.isupper():
                raise ValueError("invalid currency")
        if self.total.currency != self.per_person.currency:
            raise ValueError("quote currencies differ")
        if self.total.minor < self.per_person.minor or not self.source:
            raise ValueError("invalid group total or source")
        # Display rounding and fees can make total differ from per_person * adults.


@dataclass(frozen=True)
class RoundTripResult:
    status: Literal["ok", "empty", "error"]
    quotes: tuple[RoundTripQuote, ...] = ()
    error: str | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in ("ok", "empty", "error"):
            raise ValueError("invalid result status")
        if (self.status == "ok") != bool(self.quotes):
            raise ValueError("only ok results contain quotes")
        if (self.status == "error") != bool(self.error):
            raise ValueError("only error results contain an error")


@dataclass(frozen=True)
class RoundTripChoice:
    group: Group
    quote: RoundTripQuote
    fare_eur: Money  # separately observed per-person fare
    flights_total_eur: Money  # observed group fare, converted once
    door_to_door_eur: Money
    flight_minutes: int
    price_overshoot_pct: float
    time_overshoot_minutes: int


@dataclass(frozen=True)
class RoundTripCombo:
    window: Window
    entry: str
    exit: str
    route: Route
    choices: tuple[RoundTripChoice, ...]
    flights_eur: Money
    internal_eur: Money
    score_eur: Money
    within_rule: bool

    @property
    def total_eur(self) -> Money:
        return self.flights_eur + self.internal_eur

    @property
    def worst_overshoot_pct(self) -> float:
        return max(c.price_overshoot_pct for c in self.choices)

    def choice(self, group_id: str) -> RoundTripChoice | None:
        return next((c for c in self.choices if c.group.id == group_id), None)


@dataclass(frozen=True)
class RoundTripRanking:
    window: Window
    combos: tuple[RoundTripCombo, ...]
    best: RoundTripCombo | None
    fairest: RoundTripCombo | None


def _passes(journey: Journey, rules: FlightRules) -> bool:
    return (
        journey.stops <= rules.max_stops
        and not set(journey.via).intersection(rules.blocked_airports)
        and all(lay.minutes <= rules.max_layover_minutes for lay in journey.layovers)
        and (not journey.self_transfer or rules.allow_self_transfer)
    )


def _eligible(
    quote: RoundTripQuote,
    group: Group,
    window: Window,
    inputs: RankingInputs,
    rules: FlightRules,
) -> bool:
    return (
        quote.request.group_id == group.id
        and quote.request.adults == group.headcount
        and quote.request.outbound_date in window.outbound_dates(inputs.flex)
        and quote.outbound.arrive.date() <= window.start
        and quote.request.return_date in window.return_dates(inputs.flex)
        and quote.outbound.destination in inputs.china_airports
        and quote.inbound.origin in inputs.china_airports
        and _passes(quote.outbound, rules)
        and _passes(quote.inbound, rules)
    )


@dataclass(frozen=True)
class _Pick:
    group: Group
    quote: RoundTripQuote
    fare: Money
    total: Money
    door: Money
    minutes: int


def rank_roundtrips(
    window: Window,
    quotes: Sequence[RoundTripQuote],
    stops: Sequence[str],
    inputs: RankingInputs,
    rules: FlightRules,
) -> RoundTripRanking:
    """Rank products of intact quotes, never independently chosen legs."""
    if not inputs.groups or not stops:
        return RoundTripRanking(window, (), None, None)
    if len({g.id for g in inputs.groups}) != len(inputs.groups):
        raise ValueError("duplicate group IDs")
    by_group = [
        [q for q in quotes if _eligible(q, group, window, inputs, rules)] for group in inputs.groups
    ]
    drafts: list[tuple[Route, tuple[_Pick, ...]]] = []
    routes: dict[tuple[str, str], Route | None] = {}
    for matched in product(*by_group):
        pairs = {
            (inputs.china_airports[q.outbound.destination], inputs.china_airports[q.inbound.origin])
            for q in matched
        }
        if len(pairs) != 1:
            continue
        entry, exit = next(iter(pairs))
        if (entry, exit) not in routes:
            routes[entry, exit] = plan_route(entry, exit, stops, window, inputs.planner)
        route = routes[entry, exit]
        if route is None:
            continue
        picks = tuple(
            _Pick(
                g,
                q,
                inputs.rates.to_eur(q.per_person),
                inputs.rates.to_eur(q.total),
                inputs.rates.to_eur(q.per_person) + route.fare_eur,
                q.outbound.total_minutes + q.inbound.total_minutes,
            )
            for g, q in zip(inputs.groups, matched, strict=True)
        )
        drafts.append((route, picks))
    own_best: dict[str, _Pick] = {}
    for _, picks in drafts:
        for pick in picks:
            current = own_best.get(pick.group.id)
            if current is None or (pick.door.minor, pick.minutes) < (
                current.door.minor,
                current.minutes,
            ):
                own_best[pick.group.id] = pick
    combos = tuple(
        sorted(
            (_combo(window, route, picks, own_best, inputs) for route, picks in drafts),
            key=lambda c: c.score_eur.minor,
        )
    )
    within = [c for c in combos if c.within_rule]
    return RoundTripRanking(
        window,
        combos,
        within[0] if within else None,
        min(within, key=lambda c: (c.worst_overshoot_pct, c.score_eur.minor), default=None),
    )


def _combo(
    window: Window,
    route: Route,
    picks: tuple[_Pick, ...],
    own_best: dict[str, _Pick],
    inputs: RankingInputs,
) -> RoundTripCombo:
    choices = tuple(_choice(p, own_best[p.group.id]) for p in picks)
    flights = sum((c.flights_total_eur for c in choices), Money.zero("EUR"))
    people = sum(c.group.headcount for c in choices)
    within = all(
        c.price_overshoot_pct <= inputs.fairness.max_price_overshoot_pct
        and c.time_overshoot_minutes <= inputs.fairness.max_time_overshoot_minutes
        for c in choices
    )
    return RoundTripCombo(
        window,
        route.entry,
        route.exit,
        route,
        choices,
        flights,
        route.fare_eur.times(people),
        flights + route.score_eur.times(people),
        within,
    )


def _choice(pick: _Pick, best: _Pick) -> RoundTripChoice:
    extra = pick.door.minor - best.door.minor
    overshoot = 0.0 if best.door.minor == 0 else extra * 100 / best.door.minor
    return RoundTripChoice(
        pick.group,
        pick.quote,
        pick.fare,
        pick.total,
        pick.door,
        pick.minutes,
        round(overshoot, 1),
        max(0, pick.minutes - best.minutes),
    )


def rank_group_quotes(
    window: Window,
    quotes: Sequence[RoundTripQuote],
    group: Group,
    inputs: RankingInputs,
    rules: FlightRules,
) -> tuple[RoundTripQuote, ...]:
    """Existing relevance order: eligible fare first, then journey duration."""
    return tuple(
        sorted(
            (q for q in quotes if _eligible(q, group, window, inputs, rules)),
            key=lambda q: (
                inputs.rates.to_eur(q.total).minor,
                q.outbound.total_minutes + q.inbound.total_minutes,
            ),
        )
    )
