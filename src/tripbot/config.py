"""Load `config/trip.yaml` into typed, validated settings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from babel import UnknownLocaleError

from tripbot import i18n
from tripbot.domain.money import Rates
from tripbot.domain.plan import MAX_STOPS, Plan
from tripbot.domain.trip import Flex, Group, Origin, Window


class ConfigError(ValueError):
    """The trip configuration is missing something or contradicts itself."""


@dataclass(frozen=True)
class FlightRules:
    max_stops: int
    max_layover_minutes: int
    blocked_airports: frozenset[str]
    allow_self_transfer: bool


@dataclass(frozen=True)
class Fairness:
    max_price_overshoot_pct: int
    max_time_overshoot_minutes: int


@dataclass(frozen=True)
class Alternative:
    """The main plan without one of its stops, for /other."""

    id: str
    drop: str


@dataclass(frozen=True)
class RouteSettings:
    stops: tuple[str, ...]
    nights: dict[str, int]
    value_of_time_eur_per_hour: int
    weather_eur_per_risk_night: int
    prefer_sleepers_eur: int
    max_train_minutes: int

    @property
    def alternatives(self) -> tuple[Alternative, ...]:
        if len(self.stops) < 2:
            return ()
        return tuple(Alternative(id=f"no-{stop}", drop=stop) for stop in self.stops)

    def alternative(self, alternative_id: str) -> Alternative | None:
        return next((alt for alt in self.alternatives if alt.id == alternative_id), None)


@dataclass(frozen=True)
class AlertRules:
    drop_pct: int
    record_low: bool
    record_low_min_eur: int


@dataclass(frozen=True)
class SummarySchedule:
    weekday: int  # Monday is 0
    hour: int
    minute: int


@dataclass(frozen=True)
class Budgets:
    google: int
    serpapi: int
    tripcom: int


@dataclass(frozen=True)
class TripConfig:
    timezone: str
    locale: str
    home_currency: str
    origins: dict[str, Origin]
    groups: tuple[Group, ...]
    windows: tuple[Window, ...]
    flex: Flex
    china_airports: dict[str, str]
    flights: FlightRules
    fairness: Fairness
    route: RouteSettings
    alerts: AlertRules
    summary: SummarySchedule
    budgets: Budgets
    fallback_rates: Rates

    def origin_of(self, group: Group) -> Origin:
        return self.origins[group.origin]

    def group(self, group_id: str) -> Group | None:
        return next((group for group in self.groups if group.id == group_id), None)

    def window(self, window_id: str) -> Window | None:
        wanted = window_id.upper()
        return next((window for window in self.windows if window.id == wanted), None)

    def city_of(self, airport: str) -> str | None:
        return self.china_airports.get(airport)

    @property
    def plan(self) -> Plan:
        return Plan(
            origins=self.origins,
            groups=self.groups,
            windows=self.windows,
            stops=self.route.stops,
            nights=self.route.nights,
        )

    def with_plan(self, plan: Plan) -> TripConfig:
        """These rules with another plan; `ConfigError` if the two contradict each other."""
        config = replace(
            self,
            origins=dict(plan.origins),
            groups=plan.groups,
            windows=plan.windows,
            route=replace(self.route, stops=plan.stops, nights=dict(plan.nights)),
        )
        validate(config)
        return config


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def load_config(path: Path) -> TripConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a mapping")
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> TripConfig:
    route_raw = raw["route"]
    plan = Plan.from_json(
        {
            "origins": raw["origins"],
            "groups": raw["groups"],
            "windows": raw["windows"],
            "stops": route_raw["stops"],
            "nights": route_raw["nights"],
        }
    )
    route = RouteSettings(
        stops=plan.stops,
        nights=dict(plan.nights),
        value_of_time_eur_per_hour=int(route_raw["value_of_time_eur_per_hour"]),
        weather_eur_per_risk_night=int(route_raw["weather_eur_per_risk_night"]),
        prefer_sleepers_eur=int(route_raw["prefer_sleepers_eur"]),
        max_train_minutes=int(route_raw["max_train_hours"]) * 60,
    )
    flights_raw = raw["flights"]
    config = TripConfig(
        timezone=str(raw["timezone"]),
        locale=str(raw["locale"]),
        home_currency=str(raw["home_currency"]),
        origins=dict(plan.origins),
        groups=plan.groups,
        windows=plan.windows,
        flex=Flex(
            outbound_days_before=int(raw["flex"]["outbound_days_before"]),
            return_days_after=int(raw["flex"]["return_days_after"]),
        ),
        china_airports={str(code): str(city) for code, city in raw["china_airports"].items()},
        flights=FlightRules(
            max_stops=int(flights_raw["max_stops"]),
            max_layover_minutes=int(flights_raw["max_layover_hours"]) * 60,
            blocked_airports=frozenset(flights_raw["blocked_airports"]),
            allow_self_transfer=bool(flights_raw["allow_self_transfer"]),
        ),
        fairness=Fairness(
            max_price_overshoot_pct=int(raw["fairness"]["max_price_overshoot_pct"]),
            max_time_overshoot_minutes=int(raw["fairness"]["max_time_overshoot_hours"]) * 60,
        ),
        route=route,
        alerts=AlertRules(
            drop_pct=int(raw["alerts"]["drop_pct"]),
            record_low=bool(raw["alerts"]["record_low"]),
            record_low_min_eur=int(raw["alerts"]["record_low_min_eur"]),
        ),
        summary=_summary(raw["summary"]),
        budgets=Budgets(
            google=int(raw["budgets"]["google"]),
            serpapi=int(raw["budgets"]["serpapi"]),
            tripcom=int(raw["budgets"]["tripcom"]),
        ),
        fallback_rates=Rates(
            per_eur={
                code: Decimal(str(rate)) for code, rate in raw["fallback_rates_per_eur"].items()
            },
            as_of=date(2026, 11, 5),
        ),
    )
    validate(config)
    return config


def _summary(raw: dict[str, Any]) -> SummarySchedule:
    weekday = str(raw["weekday"]).lower()
    if weekday not in WEEKDAYS:
        raise ConfigError(f"summary.weekday must be one of {', '.join(WEEKDAYS)}")
    hour, minute = (int(part) for part in str(raw["time"]).split(":"))
    return SummarySchedule(weekday=WEEKDAYS.index(weekday), hour=hour, minute=minute)


def validate(config: TripConfig) -> None:
    for check in (_check_locale, _check_groups, _check_windows, _check_route, _check_rates):
        check(config)


def _check_locale(config: TripConfig) -> None:
    try:
        i18n.load(config.locale)
    except (ValueError, UnknownLocaleError) as error:
        raise ConfigError(f"locale {config.locale}: {error}") from error


def _check_groups(config: TripConfig) -> None:
    group_ids = [group.id for group in config.groups]
    if len(set(group_ids)) != len(group_ids):
        raise ConfigError("group ids must be unique")
    for group in config.groups:
        if group.origin not in config.origins:
            raise ConfigError(f"group {group.id} uses unknown origin {group.origin}")
        if not group.people:
            raise ConfigError(f"group {group.id} has no people")


def _check_windows(config: TripConfig) -> None:
    for window in config.windows:
        if window.end <= window.start:
            raise ConfigError(f"window {window.id} ends before it starts")


def _check_route(config: TripConfig) -> None:
    stops = config.route.stops
    if not stops:
        raise ConfigError("the route has no stops")
    if len(stops) > MAX_STOPS:
        raise ConfigError(f"the route has {len(stops)} stops; ranking handles at most {MAX_STOPS}")
    for stop in stops:
        if stop not in config.route.nights:
            raise ConfigError(f"route stop {stop} has no nights")
    for window in config.windows:
        if window.nights < len(stops):
            raise ConfigError(f"window {window.id} is too short for {len(stops)} stops")


def _check_rates(config: TripConfig) -> None:
    currencies = {origin.currency for origin in config.origins.values()}
    missing = currencies - {"EUR"} - set(config.fallback_rates.per_eur)
    if missing:
        raise ConfigError(f"fallback rates missing for {', '.join(sorted(missing))}")
