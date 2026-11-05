from tripbot.domain.money import Money
from tripbot.reference import Reference


def test_both_ways_legs_exist_in_both_directions(reference: Reference) -> None:
    there = reference.legs.between("guangzhou", "yangshuo")
    back = reference.legs.between("yangshuo", "guangzhou")
    assert there == back
    assert there[0].fare == Money.of("191", "CNY")


def test_directional_sleepers_stay_one_way(reference: Reference) -> None:
    north = [option.name for option in reference.legs.between("yangshuo", "shanghai")]
    south = [option.name for option in reference.legs.between("shanghai", "yangshuo")]
    assert "D196" in north
    assert "D196" not in south


def test_d919_runs_friday_to_monday(reference: Reference) -> None:
    d919 = next(o for o in reference.legs.between("beijing", "yangshuo") if o.name == "D919")
    assert d919.runs_on == frozenset({4, 5, 6, 0})
    assert d919.overnight


def test_climate_risk_is_higher_in_august_in_guilin(reference: Reference) -> None:
    assert reference.climate.risk("guilin", 8) > reference.climate.risk("guilin", 9)
    assert reference.climate.risk("nowhere", 8) == 0.0


def test_every_route_stop_is_a_known_city(reference: Reference) -> None:
    for city in ("beijing", "shanghai", "zhangjiajie", "yangshuo"):
        assert city in reference.cities
