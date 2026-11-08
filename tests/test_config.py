from pathlib import Path
from typing import Any

import pytest
import yaml

from tripbot.config import ConfigError, TripConfig, parse_config

ROOT = Path(__file__).resolve().parent.parent


def raw_config() -> dict[str, Any]:
    raw = yaml.safe_load((ROOT / "config" / "trip.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_repo_config_loads(config: TripConfig) -> None:
    assert [group.id for group in config.groups] == ["thiago", "lon", "sao"]
    assert sum(group.headcount for group in config.groups) == 5
    assert [window.id for window in config.windows] == ["C", "A", "B"]
    assert config.origin_of(config.groups[1]).airports == ("LHR", "LGW")
    assert config.flights.max_layover_minutes == 480
    assert config.summary.weekday == 6
    assert config.city_of("PKX") == "beijing"
    assert config.window("b") == config.windows[2]


def test_unknown_origin_is_rejected() -> None:
    raw = raw_config()
    raw["groups"][0]["origin"] = "XXX"
    with pytest.raises(ConfigError, match="unknown origin"):
        parse_config(raw)


def test_a_person_belongs_to_one_group_and_window_ids_are_unique() -> None:
    raw = raw_config()
    raw["groups"][1]["people"] = ["Luísa", "thiago"]
    with pytest.raises(ConfigError, match="only one group"):
        parse_config(raw)
    raw = raw_config()
    raw["windows"][1]["id"] = raw["windows"][0]["id"]
    with pytest.raises(ConfigError, match="window ids"):
        parse_config(raw)


def test_other_plans_drop_each_stop_in_turn(config: TripConfig) -> None:
    assert [alt.drop for alt in config.route.alternatives] == list(config.route.stops)
    raw = raw_config()
    raw["route"]["stops"] = ["beijing"]
    assert parse_config(raw).route.alternatives == ()


def test_a_route_needs_a_stop() -> None:
    raw = raw_config()
    raw["route"]["stops"] = []
    with pytest.raises(ConfigError, match="no stops"):
        parse_config(raw)


def test_every_origin_currency_needs_a_fallback_rate() -> None:
    raw = raw_config()
    del raw["fallback_rates_per_eur"]["BRL"]
    with pytest.raises(ConfigError, match="BRL"):
        parse_config(raw)


def test_a_locale_without_a_catalog_is_rejected() -> None:
    raw = raw_config()
    raw["locale"] = "de_DE"
    with pytest.raises(ConfigError, match="de_DE"):
        parse_config(raw)
