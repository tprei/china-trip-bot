from dataclasses import replace
from datetime import datetime

import pytest
from factories import eur, offer

from tripbot.config import TripConfig
from tripbot.domain.flights import Offer
from tripbot.domain.legs import LegOption, LegTable
from tripbot.domain.money import Money
from tripbot.domain.ranking import RankingInputs, rank_window
from tripbot.reference import Reference, ranking_inputs

OUT = datetime(2027, 8, 29, 9, 0)  # window B starts Mon 30 Aug
BACK = datetime(2027, 9, 10, 12, 0)
HOME = {"thiago": "DUB", "lon": "LHR", "sao": "GRU"}
FREE_TRAIN = LegOption(mode="train", name="Free", minutes=0, fare=Money.zero("EUR"))


@pytest.fixture
def ranking(config: TripConfig, reference: Reference) -> RankingInputs:
    return ranking_inputs(config, reference, config.fallback_rates)


@pytest.fixture
def flights_only(ranking: RankingInputs) -> RankingInputs:
    """Internal travel, time and weather cost nothing, so only flights matter."""
    planner = replace(
        ranking.planner,
        legs=LegTable(options={}, fallback_flight=FREE_TRAIN),
        value_of_time_eur_per_hour=0,
        weather_eur_per_risk_night=0,
        prefer_sleepers_eur=0,
    )
    return replace(ranking, planner=planner)


def round_trip(group_id: str, china: str, out_eur: int, back_eur: int) -> list[Offer]:
    home = HOME[group_id]
    return [
        offer(group_id, "outbound", [home, china], OUT, eur(out_eur)),
        offer(group_id, "return", [china, home], BACK, eur(back_eur)),
    ]


def everyone(china: str, prices: dict[str, int]) -> list[Offer]:
    offers: list[Offer] = []
    for group_id, price in prices.items():
        offers += round_trip(group_id, china, price, price)
    return offers


def pair(combo_entry: str, combo_exit: str) -> tuple[str, str]:
    return (combo_entry, combo_exit)


def test_cheapest_city_pair_wins_and_totals_add_up(
    config: TripConfig, flights_only: RankingInputs
) -> None:
    offers = everyone("PEK", {"thiago": 400, "lon": 350, "sao": 800})
    offers += everyone("CAN", {"thiago": 350, "lon": 300, "sao": 700})
    result = rank_window(config.windows[2], offers, config.route.stops, flights_only)

    assert result.best is not None
    assert pair(result.best.entry, result.best.exit) == ("guangzhou", "guangzhou")
    assert result.best.flights_eur == eur((350 + 300 * 2 + 700 * 2) * 2)
    assert result.best.total_eur == result.best.flights_eur
    assert len(result.combos) == 4


def test_open_jaw_wins_when_it_saves_internal_travel(
    config: TripConfig, ranking: RankingInputs
) -> None:
    offers = everyone("PEK", {"thiago": 400, "lon": 350, "sao": 800})
    offers += everyone("CAN", {"thiago": 350, "lon": 300, "sao": 700})
    result = rank_window(config.windows[2], offers, config.route.stops, ranking)

    assert result.best is not None
    assert result.best.entry != result.best.exit
    assert result.best.internal_eur.minor > 0
    assert result.best.total_eur == result.best.flights_eur + result.best.internal_eur


def test_a_combo_that_overcharges_one_group_is_out_of_the_rule(
    config: TripConfig, flights_only: RankingInputs
) -> None:
    offers = everyone("PEK", {"thiago": 300, "lon": 300, "sao": 700})
    # Guangzhou both ways is cheapest in total but costs Thiago a third more.
    offers += everyone("CAN", {"thiago": 400, "lon": 250, "sao": 600})
    result = rank_window(config.windows[2], offers, config.route.stops, flights_only)

    cheapest = result.combos[0]
    assert pair(cheapest.entry, cheapest.exit) == ("guangzhou", "guangzhou")
    assert not cheapest.within_rule
    thiago = cheapest.choice("thiago")
    assert thiago is not None and thiago.price_overshoot_pct == 33.3
    assert result.best is not None and result.best.within_rule
    assert result.best.entry != result.best.exit


def test_fairest_minimises_the_worst_overshoot(
    config: TripConfig, flights_only: RankingInputs
) -> None:
    offers = everyone("PEK", {"thiago": 330, "lon": 330, "sao": 800})
    offers += everyone("CAN", {"thiago": 300, "lon": 300, "sao": 900})
    result = rank_window(config.windows[2], offers, config.route.stops, flights_only)

    assert result.best is not None and result.fairest is not None
    assert pair(result.best.entry, result.best.exit) == ("beijing", "beijing")
    assert result.fairest.entry != result.fairest.exit
    assert result.fairest.worst_overshoot_pct < result.best.worst_overshoot_pct


def test_flights_landing_after_the_first_day_are_ignored(
    config: TripConfig, flights_only: RankingInputs
) -> None:
    late = datetime(2027, 8, 30, 20, 0)  # lands 31 Aug
    offers = everyone("PEK", {"thiago": 400, "lon": 400, "sao": 900})
    offers.append(offer("thiago", "outbound", ["DUB", "PVG"], late, eur(100)))
    offers += round_trip("lon", "PVG", 100, 100)
    offers += round_trip("sao", "PVG", 100, 100)
    result = rank_window(config.windows[2], offers, config.route.stops, flights_only)
    assert all(combo.entry != "shanghai" for combo in result.combos)


def test_no_shared_city_means_no_combo(config: TripConfig, flights_only: RankingInputs) -> None:
    offers = round_trip("thiago", "PEK", 400, 400) + round_trip("lon", "CAN", 300, 300)
    offers += round_trip("sao", "PVG", 700, 700)
    result = rank_window(config.windows[2], offers, config.route.stops, flights_only)
    assert result.combos == ()
    assert result.best is None and result.fairest is None
