"""Load the reference facts in `data/` into domain types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tripbot.config import TripConfig
from tripbot.domain.legs import LegOption, LegTable, Mode
from tripbot.domain.money import Money, Rates
from tripbot.domain.routes import City, PlannerInputs
from tripbot.domain.weather import Climate, MonthClimate, Typhoon

WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODES: dict[str, Mode] = {"train": "train", "sleeper": "sleeper", "flight": "flight"}
TYPHOON_LEVELS: dict[str, Typhoon] = {"low": "low", "moderate": "moderate", "high": "high"}


@dataclass(frozen=True)
class Reference:
    cities: dict[str, City]
    legs: LegTable
    climate: Climate


def load_reference(data_dir: Path) -> Reference:
    return Reference(
        cities=load_cities(data_dir / "cities.yaml"),
        legs=load_legs(data_dir / "legs.yaml"),
        climate=load_climate(data_dir / "weather.yaml"),
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
    )


def _read(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_cities(path: Path) -> dict[str, City]:
    raw = _read(path)
    return {
        city_id: City(
            id=city_id,
            name=str(spec["name"]),
            region=str(spec["region"]),
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
