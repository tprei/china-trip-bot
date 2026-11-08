from dataclasses import replace
from datetime import date

from tripbot.config import TripConfig
from tripbot.domain.routes import PlannerInputs, Route, plan_route, stops_without
from tripbot.domain.trip import Window


def nights_used(route: Route) -> int:
    return sum(stay.nights for stay in route.stays) + route.sleepers


def test_main_plan_fills_the_window_and_ends_at_the_exit(
    config: TripConfig, inputs: PlannerInputs
) -> None:
    window = config.windows[2]
    route = plan_route("guangzhou", "beijing", config.route.stops, window, inputs)
    assert route is not None
    assert nights_used(route) == window.nights
    assert route.legs[0].origin == "guangzhou"
    assert route.order[-1] == "beijing"
    assert set(route.order) == set(config.route.stops)


def test_trains_over_the_cap_give_way_to_shorter_options(
    config: TripConfig, inputs: PlannerInputs
) -> None:
    uncapped = replace(inputs, max_train_minutes=24 * 60)
    stops = ("beijing", "shanghai")
    sleeper = plan_route("beijing", "beijing", stops, config.windows[1], uncapped)
    day_train = plan_route("beijing", "beijing", stops, config.windows[1], inputs)
    assert sleeper is not None and day_train is not None
    assert "sleeper" in {leg.option.mode for leg in sleeper.legs}
    assert all(leg.option.minutes <= 8 * 60 for leg in day_train.legs)

    window = Window(id="Y", start=date(2027, 9, 1), end=date(2027, 9, 5))
    route = plan_route("yangshuo", "shanghai", ("yangshuo", "shanghai"), window, inputs)
    assert route is not None
    assert [leg.option.mode for leg in route.legs] == ["flight"]


def test_d919_only_runs_friday_to_monday(inputs: PlannerInputs) -> None:
    two_each = replace(inputs, nights={"beijing": 2, "yangshuo": 2}, max_train_minutes=24 * 60)
    stops = ("beijing", "yangshuo")

    leaves_friday = Window(id="X", start=date(2027, 9, 1), end=date(2027, 9, 6))
    friday = plan_route("beijing", "yangshuo", stops, leaves_friday, two_each)
    assert friday is not None
    assert friday.legs[0].day.weekday() == 4
    assert friday.legs[0].option.name == "D919"

    leaves_wednesday = Window(id="Y", start=date(2027, 8, 30), end=date(2027, 9, 3))
    wednesday = plan_route("beijing", "yangshuo", stops, leaves_wednesday, two_each)
    assert wednesday is not None
    assert wednesday.legs[0].day.weekday() == 2
    assert wednesday.legs[0].option.mode != "sleeper"


def test_unlisted_city_pairs_use_the_fallback_flight(
    config: TripConfig, inputs: PlannerInputs
) -> None:
    route = plan_route("chengdu", "beijing", ("beijing",), config.windows[0], inputs)
    assert route is not None
    assert route.legs[0].option == inputs.legs.fallback_flight


def test_a_window_too_short_for_the_stops_has_no_route(inputs: PlannerInputs) -> None:
    short = Window(id="S", start=date(2027, 9, 1), end=date(2027, 9, 3))
    stops = ("beijing", "shanghai", "zhangjiajie", "yangshuo")
    assert plan_route("beijing", "beijing", stops, short, inputs) is None


def test_september_nights_in_the_south_cost_less_weather(inputs: PlannerInputs) -> None:
    stops = ("yangshuo",)
    august = plan_route(
        "guilin", "guilin", stops, Window("A", date(2027, 8, 2), date(2027, 8, 5)), inputs
    )
    september = plan_route(
        "guilin", "guilin", stops, Window("S", date(2027, 9, 6), date(2027, 9, 9)), inputs
    )
    assert august is not None and september is not None
    assert september.weather_eur < august.weather_eur


def test_alternatives_drop_one_stop(config: TripConfig) -> None:
    assert stops_without(config.route.stops, "shanghai") == ("beijing", "zhangjiajie", "yangshuo")
