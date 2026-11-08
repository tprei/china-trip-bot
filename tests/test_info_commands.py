import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from factories import eur
from telegram_fakes import FakeDeck, FakeMessenger, World, bot, message

from tripbot import i18n
from tripbot.config import AlertRules, TripConfig
from tripbot.domain.alerts import decide_alerts
from tripbot.domain.schedule import train_sale_dates
from tripbot.reference import Reference
from tripbot.store import repo
from tripbot.telegram.bot import BotDeps, handle_update
from tripbot.telegram.info import seed_todos

NOW = datetime(2026, 11, 8, 10, 0)


@pytest.fixture
def chat(config: TripConfig, reference: Reference, world: World) -> tuple[BotDeps, FakeMessenger]:
    conn, _ = world
    return bot(config, reference, conn, FakeDeck())


def ask(chat: tuple[BotDeps, FakeMessenger], text: str) -> str:
    deps, messenger = chat
    handle_update(deps, message(text), None, NOW)
    return str(messenger.sent[-1].body)


def test_flights_lists_a_groups_options_in_its_currency(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    reply = ask(chat, "/flights luísa B")
    assert "Luísa + Gabi · window B" in reply
    assert "<b>Out</b>" in reply and "<b>Back</b>" in reply
    assert "£" in reply
    assert ask(chat, "/flights").startswith("Who?")


def test_cities_and_people(chat: tuple[BotDeps, FakeMessenger]) -> None:
    assert "Entry × exit · window B" in ask(chat, "/cities b")
    assert "5 people" in ask(chat, "/people")
    assert "serpapi · 54/54" in ask(chat, "/sources")


def test_about_walks_the_route_in_order_and_finds_cities_by_name(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    deps, _ = chat
    context = deps.context(NOW)
    window = context.best_window()
    assert window is not None
    best = context.rankings().main[window].best
    assert best is not None
    reply = ask(chat, "/about")
    headings = [line.split("</b>")[0].removeprefix("<b>") for line in reply.split("\n\n")]
    assert headings == [context.reference.cities[city].name for city in best.route.order]
    assert "• Grande Muralha em Mutianyu" in reply
    assert ask(chat, "/about XI'AN").startswith("<b>Xi'an</b>\n")
    assert ask(chat, "/about lhasa").startswith("No guide for lhasa. Try one of: Changsha")


def test_links_list_every_ticket_and_add_up_to_the_group_total(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    deps, _ = chat
    best = deps.context(NOW).rankings().main["A"].best
    assert best is not None
    reply = ask(chat, "/links a")
    flights = [line for line in reply.splitlines() if line.startswith(("out ", "back "))]
    assert len(flights) == 2 * len(best.choices)
    assert all('<a href="https://www.google.com/travel/flights/search?tfs=' in f for f in flights)
    assert reply.count("TBD link not available yet") == len(best.route.legs)
    running = [line.rsplit("→ ", 1)[1] for line in reply.splitlines() if " = " in line]
    assert len(running) == len(best.choices) + 1
    assert running[-1] == i18n.money(best.total_eur)
    assert reply.endswith(f"<b>Group total: {i18n.money(best.total_eur)}</b>")


def test_trains_and_sleepers_come_from_the_reference_table(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    trains = ask(chat, "/trains beijing shanghai")
    assert "D7 / D5 / D9" in trains and "Trem rápido G" in trains
    assert "Zhangjiajie" not in trains
    sleepers = ask(chat, "/sleepers")
    assert "D919" in sleepers and "Mon/Fri/Sat/Sun" in sleepers
    assert "est." in ask(chat, "/domestic")


def test_visa_shows_its_dates_and_steps(chat: tuple[BotDeps, FakeMessenger]) -> None:
    reply = ask(chat, "/visa")
    assert reply.startswith("<b>Visto necessário")
    assert "Tue 1 Dec" in reply


def test_weather_and_reminders_follow_the_best_route(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    assert "nights in" in ask(chat, "/weather B")
    reminders = ask(chat, "/reminders B")
    assert "visa decision" in reminders and "tickets open" in reminders
    deps, _ = chat
    best = deps.context(NOW).rankings().main["B"].best
    assert best is not None
    opening_date = i18n.day(train_sale_dates(best.route)[0].opens)
    assert opening_date in reminders


def test_todo_is_seeded_once_then_edited(chat: tuple[BotDeps, FakeMessenger]) -> None:
    deps, _ = chat
    seed_todos(deps.context(NOW))
    seed_todos(deps.context(NOW))
    assert len(repo.todos(deps.conn)) == 3
    ask(chat, "/todo add Book the <D7> sleeper")
    listed = ask(chat, "/todo done 1")
    assert len(repo.todos(deps.conn)) == 3
    assert "Book the &lt;D7&gt; sleeper" in listed
    assert ask(chat, "/todo done 9") == "No task with that number."


def test_target_is_stored_and_shown(chat: tuple[BotDeps, FakeMessenger]) -> None:
    assert ask(chat, "/target €5.000") == "Target set: €5,000"
    assert "Target €5,000" in ask(chat, "/alerts")


def test_help_lists_the_text_commands(chat: tuple[BotDeps, FakeMessenger]) -> None:
    reply = ask(chat, "/help")
    for name in ("/visa", "/weather", "/todo", "/trains", "/other"):
        assert name in reply


def test_banner_sends_the_saved_poster(chat: tuple[BotDeps, FakeMessenger], tmp_path: Path) -> None:
    deps, messenger = chat
    assert ask(chat, "/banner") == "No banner yet."
    deps.banner_path = tmp_path / "banner.png"
    deps.banner_path.write_bytes(b"\x89PNG poster")
    handle_update(deps, message("/banner"), None, NOW)
    assert (messenger.sent[-1].kind, messenger.sent[-1].body) == ("photo", b"\x89PNG poster")


def test_target_alert_fires_only_when_crossing() -> None:
    rules = AlertRules(drop_pct=5, record_low=False, record_low_min_eur=50)
    crossing = decide_alerts({"B": eur(4900)}, {"B": [eur(5100)]}, None, rules, set(), eur(5000))
    assert [a.kind for a in crossing] == ["target"]
    staying = decide_alerts({"B": eur(4900)}, {"B": [eur(4950)]}, None, rules, set(), eur(5000))
    assert staying == []
    fresh = decide_alerts({"B": eur(4900)}, {"B": []}, None, rules, set(), eur(5000))
    assert fresh == []


def test_unknown_commands_are_ignored(chat: tuple[BotDeps, FakeMessenger]) -> None:
    deps, messenger = chat
    handle_update(deps, message("/nope"), None, NOW)
    assert messenger.sent == []
    assert isinstance(deps.conn, sqlite3.Connection)
