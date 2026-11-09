"""Combine everyone's flights with a route and rank the combinations fairly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tripbot.config import Fairness
from tripbot.domain.flights import Offer
from tripbot.domain.money import Money, Rates
from tripbot.domain.routes import PlannerInputs, Route, plan_route
from tripbot.domain.trip import Flex, Group, Window

WEEKEND = frozenset({4, 5, 6})  # Friday, Saturday, Sunday, as `date.weekday()` numbers them


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
    home_airports: Mapping[str, tuple[str, ...]]  # group id -> the airports it flies from
    flex: Flex
    fairness: Fairness
    china_airports: dict[str, str]
    planner: PlannerInputs
    rates: Rates


def rank_window(
    window: Window, offers: Sequence[Offer], stops: Sequence[str], inputs: RankingInputs
) -> WindowRanking:
    """Every entry × exit city pair all groups can reach on some slide of the window, best first.

    Each combo carries the slid window it was priced on; the ranking keeps the configured one.
    A slid combo is fair when no group pays much more than its best option on the configured
    dates or on that slide, so the margin can add plans but never takes the configured one away.
    """
    slides = {
        variant: _drafts(variant, offers, stops, inputs) for variant in window.variants(inputs.flex)
    }
    configured = slides[window]
    combos = [
        combo
        for variant, drafts in slides.items()
        for combo in _combos(variant, drafts, _own_best(configured + drafts), inputs)
    ]
    ordered = sorted(
        combos,
        key=lambda combo: (combo.score_eur.minor, abs((combo.window.start - window.start).days)),
    )
    within = [combo for combo in ordered if combo.within_rule]
    best = within[0] if within else None
    fairest = min(within, key=lambda c: (c.worst_overshoot_pct, c.score_eur.minor), default=None)
    return WindowRanking(window=window, combos=_one_per_pair(ordered), best=best, fairest=fairest)


def _drafts(
    window: Window, offers: Sequence[Offer], stops: Sequence[str], inputs: RankingInputs
) -> list[_Draft]:
    """Each city pair every group can fly on these dates, with its route and everyone's flights."""
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

    drafts: list[_Draft] = []
    for entry in sorted(entries):
        for exit in sorted(exits):
            route = plan_route(entry, exit, stops, window, inputs.planner)
            if route is None:
                continue
            picks = []
            for group in inputs.groups:
                out, back = outbound[group.id][entry], inbound[group.id][exit]
                fare = inputs.rates.to_eur(out.price) + inputs.rates.to_eur(back.price)
                minutes = out.total_minutes + back.total_minutes
                picks.append(_Pick(group, out, back, fare, fare + route.fare_eur, minutes))
            drafts.append(_Draft(entry, exit, route, tuple(picks)))
    return drafts


def _one_per_pair(ordered: Sequence[Combo]) -> tuple[Combo, ...]:
    """Each entry × exit pair once, at its best slide: within the fairness rule when one is."""
    kept: dict[tuple[str, str], int] = {}
    for index, combo in sorted(enumerate(ordered), key=lambda item: not item[1].within_rule):
        kept.setdefault((combo.entry, combo.exit), index)
    return tuple(ordered[index] for index in sorted(kept.values()))


def _outbound(
    offers: Sequence[Offer], group: Group, window: Window, inputs: RankingInputs
) -> list[Offer]:
    """The group's flights out, from its own home: offers stay keyed by group id when it moves."""
    dates = set(window.outbound_dates(inputs.flex))
    home = inputs.home_airports[group.id]
    return [
        offer
        for offer in offers
        if offer.group_id == group.id
        and offer.direction == "outbound"
        and offer.origin in home
        and offer.depart.date() in dates
        and offer.arrive.date() <= window.start
    ]


def _inbound(
    offers: Sequence[Offer], group: Group, window: Window, inputs: RankingInputs
) -> list[Offer]:
    dates = set(window.return_dates(inputs.flex))
    home = inputs.home_airports[group.id]
    return [
        offer
        for offer in offers
        if offer.group_id == group.id
        and offer.direction == "return"
        and offer.destination in home
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
    price = inputs.rates.to_eur(offer.price) + _weekday_penalty(offer, inputs)
    return (price.minor, offer.total_minutes)


def _weekday_penalty(offer: Offer, inputs: RankingInputs) -> Money:
    """Per person: a return leaving China Monday to Thursday ranks as if it cost this much more."""
    if offer.direction == "return" and offer.depart.weekday() not in WEEKEND:
        return Money.of(inputs.flex.weekday_return_eur, "EUR")
    return Money.zero("EUR")


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


@dataclass(frozen=True)
class _Draft:
    entry: str
    exit: str
    route: Route
    picks: tuple[_Pick, ...]


def _own_best(drafts: Sequence[_Draft]) -> dict[str, _Pick]:
    """Each group's cheapest door-to-door pick among these drafts: what fairness compares to."""
    own_best: dict[str, _Pick] = {}
    for draft in drafts:
        for pick in draft.picks:
            current = own_best.get(pick.group.id)
            if current is None or pick.door < current.door:
                own_best[pick.group.id] = pick
    return own_best


def _combos(
    window: Window, drafts: Sequence[_Draft], own_best: dict[str, _Pick], inputs: RankingInputs
) -> list[Combo]:
    people = sum(group.headcount for group in inputs.groups)
    combos = []
    for draft in drafts:
        choices = tuple(_choice(pick, own_best[pick.group.id]) for pick in draft.picks)
        flights = sum((choice.flights_total_eur for choice in choices), Money.zero("EUR"))
        weekdays = sum(
            (
                _weekday_penalty(pick.inbound, inputs).times(pick.group.headcount)
                for pick in draft.picks
            ),
            Money.zero("EUR"),
        )
        within = all(
            choice.price_overshoot_pct <= inputs.fairness.max_price_overshoot_pct
            and choice.time_overshoot_minutes <= inputs.fairness.max_time_overshoot_minutes
            for choice in choices
        )
        combos.append(
            Combo(
                window=window,
                entry=draft.entry,
                exit=draft.exit,
                route=draft.route,
                choices=choices,
                flights_eur=flights,
                internal_eur=draft.route.fare_eur.times(people),
                score_eur=flights + draft.route.score_eur.times(people) + weekdays,
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
