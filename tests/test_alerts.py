from datetime import date, datetime

from factories import eur

from tripbot.config import AlertRules, TripConfig
from tripbot.domain.alerts import decide_alerts
from tripbot.domain.routes import PlannerInputs, plan_route
from tripbot.domain.schedule import summary_due, train_sale_dates, week_key

RULES = AlertRules(drop_pct=5, record_low=True, record_low_min_eur=50)


def test_a_new_record_low_alerts() -> None:
    alerts = decide_alerts({"B": eur(5420)}, {"B": [eur(5651), eur(5598)]}, None, RULES, set())
    assert [(a.kind, a.previous) for a in alerts] == [("record_low", eur(5598))]
    assert alerts[0].saving == eur(178)


def test_a_five_percent_drop_since_the_summary_alerts() -> None:
    history = {"A": [eur(5000), eur(5900)]}
    alerts = decide_alerts({"A": eur(5600)}, history, {"A": eur(5900)}, RULES, set())
    assert [(a.kind, a.window_id) for a in alerts] == [("drop", "A")]
    assert alerts[0].saving_pct == 5.1


def test_small_drops_and_first_runs_stay_quiet() -> None:
    assert decide_alerts({"A": eur(5700)}, {"A": [eur(5000)]}, {"A": eur(5900)}, RULES, set()) == []
    assert decide_alerts({"A": eur(4000)}, {}, None, RULES, set()) == []


def test_a_record_low_must_beat_the_previous_low_by_the_minimum() -> None:
    history = {"B": [eur(5600)]}
    assert decide_alerts({"B": eur(5551)}, history, None, RULES, set()) == []
    alerts = decide_alerts({"B": eur(5550)}, history, None, RULES, set())
    assert [a.kind for a in alerts] == ["record_low"]


def test_a_small_record_low_can_still_be_a_drop() -> None:
    history = {"A": [eur(5320)]}
    alerts = decide_alerts({"A": eur(5300)}, history, {"A": eur(5600)}, RULES, set())
    assert [(a.kind, a.previous) for a in alerts] == [("drop", eur(5600))]


def test_one_alert_per_window_per_day() -> None:
    history = {"B": [eur(5651)]}
    assert decide_alerts({"B": eur(5000)}, history, None, RULES, {"B"}) == []


def test_summary_is_due_once_a_week_after_its_hour(config: TripConfig) -> None:
    sunday_morning = datetime(2026, 11, 8, 9, 5)
    assert summary_due(sunday_morning, config.summary, last_week=None)
    assert not summary_due(sunday_morning, config.summary, last_week=week_key(date(2026, 11, 8)))
    assert not summary_due(datetime(2026, 11, 8, 8, 59), config.summary, last_week=None)
    assert not summary_due(datetime(2026, 11, 9, 10, 0), config.summary, last_week=None)


def test_train_tickets_open_fourteen_days_before(config: TripConfig, inputs: PlannerInputs) -> None:
    route = plan_route("guangzhou", "beijing", config.route.stops, config.windows[2], inputs)
    assert route is not None
    sales = train_sale_dates(route)
    first = sales[0]
    assert (first.origin, first.destination) == ("guangzhou", "yangshuo")
    assert first.travel_day == date(2027, 8, 30)
    assert first.opens == date(2027, 8, 16)
    assert any(sale.overnight for sale in sales)
