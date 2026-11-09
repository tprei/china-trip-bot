from dataclasses import replace
from datetime import date, datetime

import pytest
from factories import eur, offer

from tripbot.config import TripConfig
from tripbot.domain.flights import Offer
from tripbot.domain.legs import LegOption, LegTable
from tripbot.domain.money import Money
from tripbot.domain.ranking import RankingInputs, rank_window
from tripbot.reference import Reference, ranking_inputs

OUT = datetime(2027, 8, 29, 9, 0)  # window B starts Mon 30 Aug
BACK = datetime(2027, 9, 12, 12, 0)
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


def test_the_trip_slides_a_day_when_that_is_cheaper(
    config: TripConfig, flights_only: RankingInputs
) -> None:
    window = config.window("B")
    assert window is not None and window.start == date(2027, 8, 30)
    friday = datetime(
        2027, 8, 27, 9, 0
    )  # only a trip starting Sun 29 Aug can leave home this early
    offers = everyone("PEK", {"thiago": 400, "lon": 400, "sao": 800})
    offers += [
        offer(group, "outbound", [home, "PEK"], friday, eur(200)) for group, home in HOME.items()
    ]
    result = rank_window(window, offers, config.route.stops, flights_only)

    assert result.window == window
    assert result.best is not None
    assert (result.best.window.start, result.best.window.end) == (
        date(2027, 8, 29),
        date(2027, 9, 11),
    )
    assert all(choice.outbound.depart == friday for choice in result.best.choices)
    pairs = [pair(combo.entry, combo.exit) for combo in result.combos]
    assert len(pairs) == len(set(pairs))


def test_a_slide_that_overcharges_one_group_keeps_the_configured_dates(
    config: TripConfig, flights_only: RankingInputs
) -> None:
    window = config.window("B")
    assert window is not None
    monday, friday = datetime(2027, 8, 30, 9, 0), datetime(2027, 8, 27, 9, 0)
    offers = [offer(group, "return", ["PEK", home], BACK, eur(400)) for group, home in HOME.items()]
    # A day earlier saves São Paulo €600 each but costs Thiago €300 more (+43%).
    prices = {"thiago": (300, 600), "lon": (400, 400), "sao": (800, 200)}
    for group, (configured, earlier) in prices.items():
        offers.append(offer(group, "outbound", [HOME[group], "PEK"], monday, eur(configured)))
        offers.append(offer(group, "outbound", [HOME[group], "PEK"], friday, eur(earlier)))
    result = rank_window(window, offers, config.route.stops, flights_only)

    assert result.best is not None and result.best.window == window
    assert result.best.total_eur == eur(700 + 800 * 2 + 1200 * 2)


@pytest.mark.parametrize(("saving", "weekday", "penalty"), [(80, 6, 0), (150, 0, 100)])
def test_a_weekday_return_must_save_more_than_the_weekend_preference(
    config: TripConfig, flights_only: RankingInputs, saving: int, weekday: int, penalty: int
) -> None:
    monday = datetime(2027, 9, 13, 12, 0)  # BACK is Sun 12 Sep
    offers = everyone("PEK", {"thiago": 400, "lon": 400, "sao": 800})
    offers.append(offer("thiago", "return", ["PEK", "DUB"], monday, eur(400 - saving)))
    result = rank_window(config.windows[2], offers, config.route.stops, flights_only)

    assert result.best is not None
    thiago = result.best.choice("thiago")
    assert thiago is not None and thiago.inbound.depart.weekday() == weekday
    paid = 400 + 400 - (saving if weekday == 0 else 0) + (400 + 400) * 2 + (800 + 800) * 2
    assert result.best.total_eur == eur(paid)
    assert result.best.score_eur == eur(paid + penalty)
