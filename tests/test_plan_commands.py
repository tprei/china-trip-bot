from datetime import date, datetime

import pytest
from telegram_fakes import FakeDeck, FakeMessenger, World, bot, message

from tripbot.config import TripConfig
from tripbot.reference import Reference
from tripbot.store.plans import recent_changes
from tripbot.telegram.bot import BotDeps, handle_update

NOW = datetime(2026, 11, 8, 10, 0)


@pytest.fixture
def chat(config: TripConfig, reference: Reference, world: World) -> tuple[BotDeps, FakeMessenger]:
    conn, _ = world
    return bot(config, reference, conn, FakeDeck())


def ask(chat: tuple[BotDeps, FakeMessenger], text: str) -> str:
    deps, messenger = chat
    handle_update(deps, message(text), None, NOW)
    return str(messenger.sent[-1].body)


def current(chat: tuple[BotDeps, FakeMessenger]) -> TripConfig:
    deps, _ = chat
    return deps.context(NOW).config


def test_someone_new_reprices_every_window_until_undone(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    reply = ask(chat, "/person add Ana lon")
    assert reply.startswith("Luísa added Ana to the group from Londres.")
    assert reply.count("(+€") == 3 and "/undo to go back." in reply
    lon = current(chat).group("lon")
    assert lon is not None and lon.people == ("Luísa", "Gabi", "Ana")

    reply = ask(chat, "/undo")
    assert reply.startswith("Luísa undid: Luísa added Ana to the group from Londres.")
    assert reply.count("(−€") == 3
    assert current(chat).plan == chat[0].base.plan
    assert ask(chat, "/undo") == "Nothing to undo."


def test_a_new_window_waits_for_tonights_search(chat: tuple[BotDeps, FakeMessenger]) -> None:
    reply = ask(chat, "/window add d 06/09 19/09")
    assert "added window D: 6 – 19 Sep\n" in reply
    assert "D no complete combo" in reply and "New flights get searched tonight." in reply
    window = current(chat).window("D")
    assert window is not None and (window.start, window.end) == (
        date(2027, 9, 6),
        date(2027, 9, 19),
    )


def test_cities_go_by_their_display_name(chat: tuple[BotDeps, FakeMessenger]) -> None:
    reply = ask(chat, "/destination nights PEQUIM 5")
    assert reply.startswith("Luísa set Pequim to 5 nights.")
    assert current(chat).route.nights["beijing"] == 5
    assert ask(chat, "/destination add Xi'an 2").startswith("Luísa added Xi'an with 2 nights.")
    assert current(chat).route.stops[-1] == "xian"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/destination add Lhasa 2", "The bot has no trains or flights for Lhasa yet."),
        ("/person move Bob lon", "Nobody called Bob is on the trip."),
        ("/group home lon XXX", "XXX isn't a home yet. Add it first: /home add XXX EUR City"),
        ("/home add LIS CHF Lisboa", "The bot converts BRL, CNY, EUR, GBP, MXN, USD;"),
        ("/window change B 12/09 30/08", "A window runs 3 to 30 nights"),
        ("/window add D 6/9", "Use /window add &lt;letter&gt;"),
        ("/window add D ²/09 19/09", "Use /window add &lt;letter&gt;"),
        ("/window add D 99999999999999999999/09 19/09", "Use /window add &lt;letter&gt;"),
        ("/window add D 01/01/2020 10/01/2020", "Window D would start before tomorrow."),
        ("/destination nights Pequim ²", "Use /destination add &lt;city&gt;"),
        ("/home add , EUR Porto", "Use /home add &lt;airports&gt;"),
        ("/home add PEK EUR Pequim", "PEK is in China; a home is where people fly from."),
        ("/person add Ana/Bia lon", "A name is one word of letters, up to 20, like Ana."),
        ("/person", "Use /person add &lt;name&gt; &lt;group&gt;"),
    ],
)
def test_a_rejected_edit_says_why_and_stores_nothing(
    chat: tuple[BotDeps, FakeMessenger], text: str, expected: str
) -> None:
    assert ask(chat, text).startswith(expected)
    assert recent_changes(chat[0].conn, 1) == []


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        ("/window add D 28/12 05/01", "28 Dec – 5 Jan"),
        ("/window add D 29/02 10/03", "29 Feb – 10 Mar"),
    ],
)
def test_days_without_a_year_are_the_next_ones(
    chat: tuple[BotDeps, FakeMessenger], text: str, dates: str
) -> None:
    assert f"added window D: {dates}\n" in ask(chat, text)


def test_a_home_with_a_slash_in_its_name_still_reads_back(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    assert ask(chat, "/home add OPO EUR Porto/Gaia").startswith(
        "Luísa added Porto/Gaia (OPO) as a home."
    )
    assert "Luísa added Porto/Gaia (OPO) as a home." in ask(chat, "/plan")
    assert ask(chat, "/undo").startswith("Luísa undid: Luísa added Porto/Gaia (OPO) as a home.")
    assert "undid: Luísa added Porto/Gaia" in ask(chat, "/plan")


def test_plan_shows_the_current_plan_and_who_changed_it(
    chat: tuple[BotDeps, FakeMessenger],
) -> None:
    ask(chat, "/group add MEX Ana Bia")
    reply = ask(chat, "/plan")
    assert "<code>mex</code> Ana + Bia · Cidade do México (MEX)" in reply
    assert "<code>B</code> 30 Aug – 12 Sep · 13 nights" in reply
    assert "Pequim 4 · Xangai 2 · Zhangjiajie 2 · Yangshuo 3" in reply
    assert "Luísa added Ana + Bia, flying from Cidade do México (MEX)." in reply
    assert reply.endswith("/person · /group · /home · /window · /destination · /undo")


@pytest.mark.usefixtures("pt_br")
def test_edits_speak_the_groups_language(chat: tuple[BotDeps, FakeMessenger]) -> None:
    reply = ask(chat, "/pessoa adicionar Ana lon")
    assert reply.startswith("Luísa adicionou Ana ao grupo de Londres.")
    assert "/desfazer para voltar." in reply
    assert ask(chat, "/plano").endswith(
        "/pessoa · /grupo · /origem · /janela · /destino · /desfazer"
    )
