import sqlite3
from dataclasses import replace
from datetime import date, datetime

import pytest
from telegram_fakes import World

from tripbot.config import ConfigError, TripConfig
from tripbot.domain.plan import (
    Plan,
    PlanError,
    add_group,
    add_origin,
    add_person,
    add_stop,
    add_window,
    change_window,
    move_person,
    rehome_group,
    remove_person,
    remove_stop,
    remove_window,
    set_nights,
)
from tripbot.domain.trip import Group
from tripbot.pipeline.state import rank_all
from tripbot.reference import Reference
from tripbot.store.plans import effective_config, recent_changes, save_change, undo

NOW = datetime(2026, 11, 8, 14, 0)
TODAY = NOW.date()
KNOWN = {"beijing", "shanghai", "zhangjiajie", "yangshuo", "guilin"}


def problem(error: pytest.ExceptionInfo[PlanError]) -> str:
    return error.value.problem


def test_the_last_person_out_takes_the_group_but_never_the_trip(config: TripConfig) -> None:
    plan, change = remove_person(config.plan, "thiago")
    assert [group.id for group in plan.groups] == ["lon", "sao"]
    assert change.subject == "Thiago" and change.detail == "thiago"
    plan, _ = remove_person(plan, "Luísa")
    plan, _ = remove_person(plan, "Gabi")
    plan, _ = remove_person(plan, "David")
    with pytest.raises(PlanError) as error:
        remove_person(plan, "Mari")
    assert problem(error) == "last_group"


def test_moving_someone_changes_both_groups(config: TripConfig) -> None:
    plan, change = move_person(config.plan, "gabi", "David")
    people = {group.id: group.people for group in plan.groups}
    assert people == {"thiago": ("Thiago",), "lon": ("Luísa",), "sao": ("David", "Mari", "Gabi")}
    assert change.detail == "sao"
    plan, _ = move_person(plan, "Thiago", "LON")
    assert [group.id for group in plan.groups] == ["lon", "sao"]
    with pytest.raises(PlanError) as error:
        move_person(plan, "Thiago", "luísa")
    assert problem(error) == "unchanged"


def test_names_are_unique_across_the_trip(config: TripConfig) -> None:
    with pytest.raises(PlanError) as error:
        add_person(config.plan, "MARI", "lon")
    assert problem(error) == "person_exists"
    with pytest.raises(PlanError) as error:
        add_group(config.plan, "MEX", ["Ana", "ana"])
    assert problem(error) == "person_exists"
    with pytest.raises(PlanError) as error:
        add_person(config.plan, "Ana", "nowhere")
    assert problem(error) == "unknown_group"


def test_a_second_group_from_the_same_home_gets_its_own_id(config: TripConfig) -> None:
    plan, change = add_group(config.plan, "LGW", ["Ana", "Bia"])
    group = plan.groups[-1]
    assert (group.id, group.origin, group.name) == ("lon2", "LON", "Ana + Bia")
    assert change.subject == "lon2"
    plan, _ = add_group(plan, "lhr", ["Caio"])
    assert plan.groups[-1].id == "lon3"
    assert plan.find_group("LON") == config.group("lon")
    two_from_london = replace(
        plan, groups=(Group("a", "LON", ("Ana",)), Group("b", "LON", ("Bia",)))
    )
    assert two_from_london.find_group("LON") is None
    assert two_from_london.find_group("bia") == two_from_london.groups[1]


def test_a_new_home_needs_real_airports_and_a_known_currency(config: TripConfig) -> None:
    currencies, china = {"EUR", "GBP"}, set(config.china_airports)
    plan, change = add_origin(config.plan, ["lis"], "eur", "Lisboa", currencies, china)
    assert plan.origins["LIS"].airports == ("LIS",) and change.detail == "Lisboa"
    for airports, currency, city, expected in (
        (["LISB"], "EUR", "X", "bad_airport"),
        ([], "EUR", "X", "bad_airport"),
        (["PEK"], "EUR", "X", "china_airport"),
        (["LGW"], "GBP", "X", "origin_exists"),
        (["OPO"], "CHF", "X", "bad_currency"),
        (["OPO"], "EUR", "P" * 41, "bad_home_name"),
    ):
        with pytest.raises(PlanError) as error:
            add_origin(config.plan, airports, currency, city, currencies, china)
        assert problem(error) == expected
    plan, _ = add_origin(config.plan, ["opo", "OPO"], "EUR", "Porto/Gaia", currencies, china)
    assert plan.origins["OPO"].airports == ("OPO",)
    for name in ("Ana/Bia", "Ana2", "A" * 21):
        with pytest.raises(PlanError) as error:
            add_person(config.plan, name, "lon")
        assert problem(error) == "bad_person"
    assert add_person(config.plan, "D'Ávila", "lon")[1].subject == "D'Ávila"


def test_windows_stay_in_date_order_and_one_always_remains(config: TripConfig) -> None:
    plan, _ = add_window(config.plan, "d", date(2027, 8, 2), date(2027, 8, 15), TODAY)
    assert [window.id for window in plan.windows] == ["D", "C", "A", "B"]
    plan, change = change_window(plan, "D", date(2027, 9, 6), date(2027, 9, 19), TODAY)
    assert [window.id for window in plan.windows] == ["C", "A", "B", "D"]
    assert change.detail == "2027-09-06/2027-09-19"
    for start, end in ((date(2027, 9, 6), date(2027, 9, 8)), (date(2027, 9, 6), date(2027, 9, 1))):
        with pytest.raises(PlanError) as error:
            change_window(plan, "D", start, end, TODAY)
        assert problem(error) == "bad_dates"
    with pytest.raises(PlanError) as error:
        add_window(plan, "AB", date(2027, 9, 6), date(2027, 9, 19), TODAY)
    assert problem(error) == "bad_window_id"
    with pytest.raises(PlanError) as error:
        add_window(plan, "E", date(2027, 9, 6), date(2027, 9, 19), TODAY)
    assert (problem(error), error.value.subject) == ("window_exists", "D")
    with pytest.raises(PlanError) as error:
        add_window(plan, "E", date(2026, 11, 8), date(2026, 11, 20), TODAY)
    assert problem(error) == "window_in_past"
    with pytest.raises(PlanError) as error:
        change_window(plan, "D", date(2027, 9, 6), date(2027, 9, 9), TODAY)
    assert problem(error) == "window_too_short"
    for window_id in ("C", "A", "B"):
        plan, _ = remove_window(plan, window_id)
    with pytest.raises(PlanError) as error:
        remove_window(plan, "D")
    assert problem(error) == "last_window"


def test_stops_take_known_cities_and_sane_nights(config: TripConfig) -> None:
    plan, _ = add_stop(config.plan, "guilin", 2, KNOWN)
    assert plan.stops[-1] == "guilin" and plan.nights["guilin"] == 2
    for city, nights, expected in (
        ("lhasa", 2, "unknown_city"),
        ("beijing", 2, "stop_exists"),
    ):
        with pytest.raises(PlanError) as error:
            add_stop(plan, city, nights, KNOWN)
        assert problem(error) == expected
    with pytest.raises(PlanError) as error:
        set_nights(plan, "guilin", 11)
    assert problem(error) == "bad_nights"
    with pytest.raises(PlanError) as error:
        add_stop(plan, "xian", 2, {*KNOWN, "xian"})
    assert (problem(error), error.value.subject) == ("too_many_stops", "5")
    four, _ = remove_stop(plan, "guilin")
    four, _ = change_window(four, "C", date(2027, 8, 9), date(2027, 8, 13), TODAY)
    with pytest.raises(PlanError) as error:
        add_stop(four, "guilin", 1, KNOWN)
    assert (problem(error), error.value.subject) == ("window_too_short", "C")
    for city in ("beijing", "shanghai", "zhangjiajie", "yangshuo"):
        plan, _ = remove_stop(plan, city)
    assert "beijing" not in plan.nights
    with pytest.raises(PlanError) as error:
        remove_stop(plan, "guilin")
    assert problem(error) == "last_stop"


def test_a_group_that_moves_is_never_priced_on_its_old_homes_flights(
    config: TripConfig, reference: Reference, world: World
) -> None:
    _, report = world
    plan, change = rehome_group(config.plan, "thiago", "LHR")
    moved = config.with_plan(plan)
    assert change.detail == "LON" and moved.group("thiago") is not None
    rankings = rank_all(moved, reference, list(report.rankings.offers), report.rankings.rates)
    assert all(ranking.best is None for ranking in rankings.main.values())


def test_a_window_key_follows_what_its_price_depends_on(config: TripConfig) -> None:
    plan = config.plan
    keys = plan.keys()
    renamed = replace(
        plan, groups=tuple(replace(g, people=tuple(p + "x" for p in g.people)) for g in plan.groups)
    )
    assert renamed.keys() == keys
    moved, _ = move_person(plan, "Gabi", "sao")
    assert all(moved.keys()[w] != keys[w] for w in keys)
    longer, _ = set_nights(plan, "beijing", 5)
    assert all(longer.keys()[w] != keys[w] for w in keys)
    shifted, _ = change_window(plan, "C", date(2027, 8, 10), date(2027, 8, 23), TODAY)
    assert [w for w in keys if shifted.keys()[w] != keys[w]] == ["C"]


def test_the_group_plan_replaces_the_files_until_undone(
    config: TripConfig, db: sqlite3.Connection
) -> None:
    assert effective_config(db, config) is config
    first = add_person(config.plan, "Ana", "lon")
    save_change(db, config, first, "Luísa", NOW)
    second = set_nights(effective_config(db, config).plan, "beijing", 5)
    save_change(db, config, second, "Thiago", NOW)
    edited = effective_config(db, config)
    lon = edited.group("lon")
    assert lon is not None and lon.people == ("Luísa", "Gabi", "Ana")
    assert edited.route.nights["beijing"] == 5
    assert Plan.from_json(edited.plan.to_json()) == edited.plan

    undone = undo(db, config, "Mari", NOW)
    assert undone is not None and undone[0].change.kind == "nights_changed"
    assert effective_config(db, config).route.nights["beijing"] == 4
    undone = undo(db, config, "Mari", NOW)
    assert undone is not None and undone[0].change.kind == "person_added"
    assert effective_config(db, config).plan == config.plan
    assert undo(db, config, "Mari", NOW) is None
    assert [c.change.kind for c in recent_changes(db, 2)] == ["undone", "undone"]


def test_an_edit_the_rules_reject_is_not_stored(config: TripConfig, db: sqlite3.Connection) -> None:
    edit = add_origin(config.plan, ["YYZ"], "CAD", "Toronto", {"CAD"}, config.china_airports)
    with pytest.raises(ConfigError, match="CAD"):
        save_change(db, config, edit, "Luísa", NOW)
    assert recent_changes(db, 1) == []
    assert effective_config(db, config) is config
