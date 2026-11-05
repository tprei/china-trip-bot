"""Combine everyone's flights with a route and rank the combinations fairly."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tripbot.config import Fairness
from tripbot.domain.flights import Offer
from tripbot.domain.money import Money, Rates
from tripbot.domain.routes import PlannerInputs, Route, plan_route
from tripbot.domain.trip import Flex, Group, Window


@dataclass(frozen=True)
class GroupChoice:
    """The flights one group takes in a combo. Money is per person, EUR."""

    group: Group
    outbound: Offer
    inbound: Offer
    fare_eur: Money
    door_to_door_eur: Money
    flight_minutes: int
    price_overshoot_pct: float
    time_overshoot_minutes: int

    @property
    def flights_total_eur(self) -> Money:
        return self.fare_eur.times(self.group.headcount)


@dataclass(frozen=True)
class Combo:
    window: Window
    entry: str
    exit: str
    route: Route
    choices: tuple[GroupChoice, ...]
    flights_eur: Money
    internal_eur: Money
    score_eur: Money
    within_rule: bool

    @property
    def total_eur(self) -> Money:
        return self.flights_eur + self.internal_eur

    @property
    def worst_overshoot_pct(self) -> float:
        return max(choice.price_overshoot_pct for choice in self.choices)

    def choice(self, group_id: str) -> GroupChoice | None:
        return next((c for c in self.choices if c.group.id == group_id), None)


@dataclass(frozen=True)
class WindowRanking:
    window: Window
    combos: tuple[Combo, ...]  # best score first
    best: Combo | None
    fairest: Combo | None


@dataclass(frozen=True)
class RankingInputs:
    groups: Sequence[Group]
    flex: Flex
    fairness: Fairness
    china_airports: dict[str, str]
    planner: PlannerInputs
    rates: Rates


def rank_window(
    window: Window, offers: Sequence[Offer], stops: Sequence[str], inputs: RankingInputs
) -> WindowRanking:
    """Every entry × exit city pair that all groups can reach, best score first."""
    outbound = {
        g.id: _cheapest_by_city(_outbound(offers, g, window, inputs), inputs, "destination")
        for g in inputs.groups
    }
    inbound = {
        g.id: _cheapest_by_city(_inbound(offers, g, window, inputs), inputs, "origin")
        for g in inputs.groups
    }
    entries = _common_cities([outbound[g.id] for g in inputs.groups])
    exits = _common_cities([inbound[g.id] for g in inputs.groups])

    drafts: list[tuple[str, str, Route]] = []
    for entry in sorted(entries):
        for exit in sorted(exits):
            route = plan_route(entry, exit, stops, window, inputs.planner)
            if route is not None:
                drafts.append((entry, exit, route))

    combos = _with_fairness(window, drafts, outbound, inbound, inputs)
    ordered = tuple(sorted(combos, key=lambda combo: combo.score_eur.minor))
    within = [combo for combo in ordered if combo.within_rule]
    best = within[0] if within else None
    fairest = min(within, key=lambda c: (c.worst_overshoot_pct, c.score_eur.minor), default=None)
    return WindowRanking(window=window, combos=ordered, best=best, fairest=fairest)


def _outbound(
    offers: Sequence[Offer], group: Group, window: Window, inputs: RankingInputs
) -> list[Offer]:
    dates = set(window.outbound_dates(inputs.flex))
    return [
        offer
        for offer in offers
        if offer.group_id == group.id
        and offer.direction == "outbound"
        and offer.depart.date() in dates
        and offer.arrive.date() <= window.start
    ]


def _inbound(
    offers: Sequence[Offer], group: Group, window: Window, inputs: RankingInputs
) -> list[Offer]:
    dates = set(window.return_dates(inputs.flex))
    return [
        offer
        for offer in offers
        if offer.group_id == group.id
        and offer.direction == "return"
        and offer.depart.date() in dates
    ]


def _cheapest_by_city(offers: list[Offer], inputs: RankingInputs, end: str) -> dict[str, Offer]:
    cheapest: dict[str, Offer] = {}
    for offer in offers:
        airport = offer.destination if end == "destination" else offer.origin
        city = inputs.china_airports.get(airport)
        if city is None:
            continue
        current = cheapest.get(city)
        if current is None or _offer_key(offer, inputs) < _offer_key(current, inputs):
            cheapest[city] = offer
    return cheapest


def _offer_key(offer: Offer, inputs: RankingInputs) -> tuple[int, int]:
    return (inputs.rates.to_eur(offer.price).minor, offer.total_minutes)


def _common_cities(per_group: list[dict[str, Offer]]) -> set[str]:
    if not per_group:
        return set()
    common = set(per_group[0])
    for cities in per_group[1:]:
        common &= set(cities)
    return common


@dataclass(frozen=True)
class _Pick:
    group: Group
    outbound: Offer
    inbound: Offer
    fare: Money
    door: Money
    minutes: int


def _with_fairness(
    window: Window,
    drafts: list[tuple[str, str, Route]],
    outbound: dict[str, dict[str, Offer]],
    inbound: dict[str, dict[str, Offer]],
    inputs: RankingInputs,
) -> list[Combo]:
    picked: list[tuple[str, str, Route, list[_Pick]]] = []
    for entry, exit, route in drafts:
        picks = []
        for group in inputs.groups:
            out, back = outbound[group.id][entry], inbound[group.id][exit]
            fare = inputs.rates.to_eur(out.price) + inputs.rates.to_eur(back.price)
            minutes = out.total_minutes + back.total_minutes
            picks.append(_Pick(group, out, back, fare, fare + route.fare_eur, minutes))
        picked.append((entry, exit, route, picks))

    own_best: dict[str, _Pick] = {}
    for _, _, _, picks in picked:
        for pick in picks:
            current = own_best.get(pick.group.id)
            if current is None or pick.door < current.door:
                own_best[pick.group.id] = pick

    people = sum(group.headcount for group in inputs.groups)
    combos = []
    for entry, exit, route, picks in picked:
        choices = tuple(_choice(pick, own_best[pick.group.id]) for pick in picks)
        flights = sum((choice.flights_total_eur for choice in choices), Money.zero("EUR"))
        within = all(
            choice.price_overshoot_pct <= inputs.fairness.max_price_overshoot_pct
            and choice.time_overshoot_minutes <= inputs.fairness.max_time_overshoot_minutes
            for choice in choices
        )
        combos.append(
            Combo(
                window=window,
                entry=entry,
                exit=exit,
                route=route,
                choices=choices,
                flights_eur=flights,
                internal_eur=route.fare_eur.times(people),
                score_eur=flights + route.score_eur.times(people),
                within_rule=within,
            )
        )
    return combos


def _choice(pick: _Pick, best: _Pick) -> GroupChoice:
    """How far this pick is from the group's own best door-to-door option."""
    extra = pick.door.minor - best.door.minor
    overshoot = 0.0 if best.door.minor == 0 else extra * 100 / best.door.minor
    return GroupChoice(
        group=pick.group,
        outbound=pick.outbound,
        inbound=pick.inbound,
        fare_eur=pick.fare,
        door_to_door_eur=pick.door,
        flight_minutes=pick.minutes,
        price_overshoot_pct=round(overshoot, 1),
        time_overshoot_minutes=max(0, pick.minutes - best.minutes),
    )
