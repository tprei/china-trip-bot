"""Plan the route inside China: stop order, nights, and the leg used each day."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import permutations

from tripbot.domain.legs import LegOption, LegTable
from tripbot.domain.money import Money, Rates
from tripbot.domain.trip import Window
from tripbot.domain.weather import Climate

MAX_NIGHT_FITTING_ROUNDS = 4


@dataclass(frozen=True)
class City:
    id: str
    name: str
    region: str
    tripcom: str | None = None


@dataclass(frozen=True)
class PlannedLeg:
    origin: str
    destination: str
    day: date
    option: LegOption
    fare_eur: Money


@dataclass(frozen=True)
class Stay:
    city: str
    arrive: date
    nights: int

    def night_dates(self) -> list[date]:
        return [self.arrive + timedelta(days=n) for n in range(self.nights)]


@dataclass(frozen=True)
class Route:
    """One person's way through China. Costs are per person, in EUR."""

    entry: str
    exit: str
    stays: tuple[Stay, ...]
    legs: tuple[PlannedLeg, ...]
    fare_eur: Money
    daytime_minutes: int
    weather_eur: Money
    score_eur: Money

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(stay.city for stay in self.stays)

    @property
    def sleepers(self) -> int:
        return sum(1 for leg in self.legs if leg.option.overnight)


@dataclass(frozen=True)
class PlannerInputs:
    legs: LegTable
    climate: Climate
    cities: Mapping[str, City]
    rates: Rates
    nights: Mapping[str, int]
    value_of_time_eur_per_hour: int
    weather_eur_per_risk_night: int
    prefer_sleepers_eur: int


def plan_route(
    entry: str, exit: str, stops: Sequence[str], window: Window, inputs: PlannerInputs
) -> Route | None:
    """The best-scoring order of `stops` between the entry and exit cities."""
    best: Route | None = None
    for order in permutations(stops):
        route = _plan_order(entry, exit, order, window, inputs)
        if route is not None and (best is None or route.score_eur < best.score_eur):
            best = route
    return best


def stops_without(stops: Sequence[str], dropped: str) -> tuple[str, ...]:
    return tuple(stop for stop in stops if stop != dropped)


def _plan_order(
    entry: str, exit: str, order: tuple[str, ...], window: Window, inputs: PlannerInputs
) -> Route | None:
    nights = [inputs.nights[city] for city in order]
    for _ in range(MAX_NIGHT_FITTING_ROUNDS):
        legs, stays = _schedule(entry, exit, order, nights, window, inputs)
        sleepers = sum(1 for leg in legs if leg.option.overnight)
        fitted = _fit_nights(nights, window.nights - sleepers)
        if fitted is None:
            return None
        if fitted == nights:
            return _price(entry, exit, stays, legs, inputs)
        nights = fitted
    return None


def _fit_nights(nights: list[int], available: int) -> list[int] | None:
    """Stretch or shrink stays so stays plus sleeper nights fill the window."""
    if available < len(nights):
        return None
    fitted = list(nights)
    by_size = sorted(range(len(fitted)), key=lambda i: (-nights[i], i))
    step = 0
    while sum(fitted) < available:
        fitted[by_size[step % len(by_size)]] += 1
        step += 1
    while sum(fitted) > available:
        largest = max(range(len(fitted)), key=lambda i: (fitted[i], -i))
        fitted[largest] -= 1
    return fitted


def _schedule(
    entry: str,
    exit: str,
    order: tuple[str, ...],
    nights: list[int],
    window: Window,
    inputs: PlannerInputs,
) -> tuple[list[PlannedLeg], list[Stay]]:
    legs: list[PlannedLeg] = []
    stays: list[Stay] = []
    day = window.start
    if entry != order[0]:
        legs.append(_choose(entry, order[0], day, inputs, allow_sleeper=False))
    for index, city in enumerate(order):
        stays.append(Stay(city=city, arrive=day, nights=nights[index]))
        leave = day + timedelta(days=nights[index])
        if index + 1 < len(order):
            leg = _choose(city, order[index + 1], leave, inputs, allow_sleeper=True)
            legs.append(leg)
            day = leave + timedelta(days=1 if leg.option.overnight else 0)
        elif exit != city:
            legs.append(_choose(city, exit, leave, inputs, allow_sleeper=False))
    return legs, stays


def _choose(
    origin: str, destination: str, day: date, inputs: PlannerInputs, *, allow_sleeper: bool
) -> PlannedLeg:
    candidates = [
        option
        for option in inputs.legs.between(origin, destination)
        if option.runs(day) and (allow_sleeper or not option.overnight)
    ] or [inputs.legs.fallback_flight]
    best = min(candidates, key=lambda option: _leg_score(option, inputs).minor)
    return PlannedLeg(
        origin=origin,
        destination=destination,
        day=day,
        option=best,
        fare_eur=inputs.rates.to_eur(best.fare),
    )


def _leg_score(option: LegOption, inputs: PlannerInputs) -> Money:
    score = inputs.rates.to_eur(option.fare) + _time_value(option.daytime_minutes(), inputs)
    if option.overnight:
        score = score - Money.of(inputs.prefer_sleepers_eur, "EUR")
    return score


def _time_value(minutes: int, inputs: PlannerInputs) -> Money:
    return Money(round(minutes * inputs.value_of_time_eur_per_hour * 100 / 60), "EUR")


def _price(
    entry: str,
    exit: str,
    stays: list[Stay],
    legs: list[PlannedLeg],
    inputs: PlannerInputs,
) -> Route:
    fare = sum((leg.fare_eur for leg in legs), Money.zero("EUR"))
    daytime = sum(leg.option.daytime_minutes() for leg in legs)
    weather = _weather_cost(stays, inputs)
    sleepers = sum(1 for leg in legs if leg.option.overnight)
    preference = Money.of(inputs.prefer_sleepers_eur, "EUR").times(sleepers)
    return Route(
        entry=entry,
        exit=exit,
        stays=tuple(stays),
        legs=tuple(legs),
        fare_eur=fare,
        daytime_minutes=daytime,
        weather_eur=weather,
        score_eur=fare + _time_value(daytime, inputs) + weather - preference,
    )


def _weather_cost(stays: list[Stay], inputs: PlannerInputs) -> Money:
    cents = 0.0
    for stay in stays:
        city = inputs.cities.get(stay.city)
        if city is None:
            continue
        for night in stay.night_dates():
            risk = inputs.climate.risk(city.region, night.month)
            cents += risk * inputs.weather_eur_per_risk_night * 100
    return Money(round(cents), "EUR")
