"""Load the reference facts in `data/` into domain types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from tripbot.config import TripConfig
from tripbot.domain.legs import LegOption, LegTable, Mode
from tripbot.domain.money import Money, Rates
from tripbot.domain.ranking import RankingInputs
from tripbot.domain.routes import City, PlannerInputs
from tripbot.domain.visa import VisaInfo
from tripbot.domain.weather import Climate, MonthClimate, Typhoon

WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODES: dict[str, Mode] = {"train": "train", "sleeper": "sleeper", "flight": "flight"}
TYPHOON_LEVELS: dict[str, Typhoon] = {"low": "low", "moderate": "moderate", "high": "high"}


@dataclass(frozen=True)
class CityGuide:
    """What a city is like and what to see there, in the group's language."""

    summary: str
    sights: tuple[str, ...]


@dataclass(frozen=True)
class Reference:
    cities: dict[str, City]
    legs: LegTable
    climate: Climate
    visa: VisaInfo
    todo_seed: tuple[str, ...]
    airports: dict[str, str]
    speeds_kmh: dict[Mode, int]
    guide: dict[str, CityGuide]


def load_reference(data_dir: Path) -> Reference:
    cities = load_cities(data_dir / "cities.yaml")
    return Reference(
        cities=cities,
        legs=load_legs(data_dir / "legs.yaml"),
        climate=load_climate(data_dir / "weather.yaml"),
        visa=load_visa(data_dir / "visa.yaml"),
        todo_seed=tuple(str(item) for item in _read(data_dir / "todo.yaml")),
        airports={str(code): str(name) for code, name in _read(data_dir / "airports.yaml").items()},
        speeds_kmh=load_speeds(data_dir / "legs.yaml"),
        guide=load_guide(data_dir / "guide.yaml", cities),
    )


def load_guide(path: Path, cities: Mapping[str, City]) -> dict[str, CityGuide]:
    """Every city the route can touch has a guide entry, so /about never comes up empty."""
    raw = _read(path)
    missing = sorted(set(cities) - set(raw))
    if missing:
        raise ValueError(f"{path.name} has no entry for {', '.join(missing)}")
    return {
        str(city): CityGuide(
            summary=str(spec["summary"]), sights=tuple(str(sight) for sight in spec["sights"])
        )
        for city, spec in raw.items()
    }


def load_speeds(path: Path) -> dict[Mode, int]:
    speeds = _read(path)["speeds_kmh"]
    if set(speeds) != set(MODES):
        raise ValueError(f"speeds_kmh needs exactly {', '.join(MODES)}")
    return {MODES[mode]: int(kmh) for mode, kmh in speeds.items()}


def load_visa(path: Path) -> VisaInfo:
    raw = _read(path)
    return VisaInfo(
        as_of=_date(raw["as_of"]),
        passport=str(raw["passport"]),
        status=str(raw["status"]),
        visa_free_until=_date(raw["visa_free_until"]),
        decide_by=_date(raw["decide_by"]),
        facts=tuple(str(fact) for fact in raw["facts"]),
        steps=tuple(str(step) for step in raw["steps"]),
        sources=tuple(str(source) for source in raw["sources"]),
    )


def planner_inputs(config: TripConfig, reference: Reference, rates: Rates) -> PlannerInputs:
    return PlannerInputs(
        legs=reference.legs,
        climate=reference.climate,
        cities=reference.cities,
        rates=rates,
        nights=config.route.nights,
        value_of_time_eur_per_hour=config.route.value_of_time_eur_per_hour,
        weather_eur_per_risk_night=config.route.weather_eur_per_risk_night,
        prefer_sleepers_eur=config.route.prefer_sleepers_eur,
        max_train_minutes=config.route.max_train_minutes,
    )


def ranking_inputs(config: TripConfig, reference: Reference, rates: Rates) -> RankingInputs:
    return RankingInputs(
        groups=config.groups,
        home_airports={group.id: config.origin_of(group).airports for group in config.groups},
        flex=config.flex,
        fairness=config.fairness,
        china_airports=config.china_airports,
        planner=planner_inputs(config, reference, rates),
        rates=rates,
    )


def _read(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _date(value: object) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def load_cities(path: Path) -> dict[str, City]:
    raw = _read(path)
    return {
        city_id: City(
            id=city_id,
            name=str(spec["name"]),
            region=str(spec["region"]),
            latitude=float(spec["at"][0]),
            longitude=float(spec["at"][1]),
            tripcom=str(spec["tripcom"]) if spec.get("tripcom") else None,
        )
        for city_id, spec in raw.items()
    }


def load_legs(path: Path) -> LegTable:
    raw = _read(path)
    options: dict[tuple[str, str], tuple[LegOption, ...]] = {}
    for leg in raw["legs"]:
        parsed = tuple(_option(spec) for spec in leg["options"])
        pairs = [(leg["from"], leg["to"])]
        if leg.get("both_ways"):
            pairs.append((leg["to"], leg["from"]))
        for pair in pairs:
            options[pair] = options.get(pair, ()) + parsed
    return LegTable(options=options, fallback_flight=_option(raw["fallback_flight"]))


def load_climate(path: Path) -> Climate:
    raw = _read(path)
    regions = {
        region: {
            int(month): MonthClimate(rain_mm=int(spec["rain_mm"]), typhoon=_typhoon(spec))
            for month, spec in months.items()
        }
        for region, months in raw["regions"].items()
    }
    return Climate(regions=regions)


def _option(spec: dict[str, Any]) -> LegOption:
    mode = MODES.get(str(spec["mode"]))
    if mode is None:
        raise ValueError(f"unknown leg mode {spec['mode']}")
    runs = spec.get("runs")
    return LegOption(
        mode=mode,
        name=str(spec["name"]),
        minutes=int(spec["minutes"]),
        overhead_minutes=int(spec.get("overhead_minutes", 0)),
        fare=_fare(spec["fare"]),
        depart=spec.get("depart"),
        arrive=spec.get("arrive"),
        runs_on=frozenset(WEEKDAY_CODES.index(day) for day in runs) if runs else None,
        cabin=spec.get("cabin"),
        estimate=bool(spec.get("estimate", False)),
    )


def _fare(spec: dict[str, Any]) -> Money:
    if len(spec) != 1:
        raise ValueError(f"a fare has exactly one currency: {spec}")
    ((currency, amount),) = spec.items()
    return Money.of(str(amount), str(currency))


def _typhoon(spec: dict[str, Any]) -> Typhoon:
    level = TYPHOON_LEVELS.get(str(spec["typhoon"]))
    if level is None:
        raise ValueError(f"unknown typhoon level {spec['typhoon']}")
    return level
