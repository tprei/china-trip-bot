import json
import re
import sqlite3
from datetime import datetime

import httpx
import pytest
from telegram_fakes import CHAT, FakeDeck, World, bot, message

from tripbot import i18n
from tripbot.config import TripConfig
from tripbot.domain.alerts import Alert
from tripbot.domain.money import Money
from tripbot.reference import Reference
from tripbot.store import repo
from tripbot.telegram.api import Button, TelegramApi, TelegramError
from tripbot.telegram.bot import handle_update, maybe_send_summary
from tripbot.telegram.commands import REGISTRY, parse
from tripbot.telegram.notify import notify_nightly

NOW = datetime(2026, 11, 8, 10, 0)


def test_parse_commands_with_and_without_the_bot_name() -> None:
    assert parse("/best b") == ("best", ["b"])
    assert parse("/BEST@trip_bot A", "trip_bot") == ("best", ["A"])
    assert parse("/best@other_bot A", "trip_bot") is None
    assert parse("hello /best") is None
    assert parse("") is None


def test_translated_command_names_are_valid_and_unique(
    pt_br: None, config: TripConfig, reference: Reference, db: sqlite3.Connection
) -> None:
    bot(config, reference, db, FakeDeck())
    names = [i18n._(spec.name) for spec in REGISTRY.values()]
    assert all(re.fullmatch(r"/[a-z0-9_]{1,32}", name) is not None for name in names)
    assert len(names) == len(set(names))


def test_translated_commands_reach_their_handlers(
    pt_br: None, config: TripConfig, reference: Reference, db: sqlite3.Connection
) -> None:
    deck = FakeDeck()
    deps, messenger = bot(config, reference, db, deck)
    handle_update(deps, message("/melhor b"), None, NOW)
    assert deck.calls == ["summary B"]

    handle_update(deps, message("/afazeres novo comprar adaptador"), None, NOW)
    assert "comprar adaptador" in str(messenger.sent[-1].body)
    handle_update(deps, message("/afazeres add x"), None, NOW)
    usage = str(messenger.sent[-1].body)
    assert "&lt;texto&gt;" in usage and "<texto>" not in usage

    handle_update(deps, message("/afazeres feito 1"), None, NOW)
    assert "Nada por fazer" in str(messenger.sent[-1].body)
    sent_count = len(messenger.sent)
    handle_update(deps, message("/best"), None, NOW)
    assert len(messenger.sent) == sent_count


def test_commands_only_answer_in_the_group(
    config: TripConfig, reference: Reference, db: sqlite3.Connection
) -> None:
    deps, messenger = bot(config, reference, db, FakeDeck())
    handle_update(deps, message("/help", chat="-999"), None, NOW)
    assert messenger.sent == []
    handle_update(deps, message("/help"), None, NOW)
    assert messenger.sent[0].kind == "text"
    assert "/other [window]" in str(messenger.sent[0].body)


def test_commands_are_answered_in_their_topic(
    config: TripConfig, reference: Reference, db: sqlite3.Connection
) -> None:
    deps, messenger = bot(config, reference, db, FakeDeck())
    deps.topic = 6
    handle_update(deps, message("/help", topic=9), None, NOW)
    handle_update(deps, message("/help"), None, NOW)
    assert [sent.topic for sent in messenger.sent] == [9, None]


def test_card_commands_send_photos_with_the_dashboard_button(
    config: TripConfig, reference: Reference, world: World
) -> None:
    conn, _ = world
    deck = FakeDeck()
    deps, messenger = bot(config, reference, conn, deck)
    for text in ("/best a", "/route", "/other B no-shanghai", "/windows"):
        handle_update(deps, message(text), None, NOW)
    assert deck.calls[0] == "summary A"
    assert deck.calls[1].startswith("route ")
    assert deck.calls[2] == "route B no-shanghai"
    assert deck.calls[3] == "windows"
    assert all(sent.kind == "photo" for sent in messenger.sent)
    assert messenger.sent[0].button == Button("Details", "https://trip.example/dash")


def test_commands_without_data_say_so(
    config: TripConfig, reference: Reference, db: sqlite3.Connection
) -> None:
    deps, messenger = bot(config, reference, db, FakeDeck())
    handle_update(deps, message("/route"), None, NOW)
    assert "No complete combo yet" in str(messenger.sent[0].body)


def test_a_crashing_command_answers_with_an_apology(
    config: TripConfig, reference: Reference, db: sqlite3.Connection
) -> None:
    deps, messenger = bot(config, reference, db, FakeDeck())
    deps.conn.close()
    handle_update(deps, message("/best"), None, NOW)
    assert messenger.sent[0].body == "Something went wrong with that command."


def test_weekly_summary_goes_out_once(
    config: TripConfig, reference: Reference, world: World
) -> None:
    conn, _ = world
    deck = FakeDeck()
    deps, messenger = bot(config, reference, conn, deck)
    deps.topic = 6
    sunday = datetime(2026, 11, 8, 9, 30)
    assert maybe_send_summary(deps, sunday)
    assert not maybe_send_summary(deps, sunday.replace(hour=11))
    assert [(sent.kind, sent.body, sent.topic) for sent in messenger.sent] == [
        ("album", (b"summary", b"map"), 6)
    ]
    assert messenger.sent[0].caption == '<a href="https://trip.example/dash">Details</a>'
    assert deck.calls == ["summary B", "map B"]
    summary = repo.last_summary(conn)
    assert summary is not None and set(summary[1]) == {"A", "B", "C"}


def test_each_summary_replaces_only_the_bots_own_pin(
    config: TripConfig, reference: Reference, world: World
) -> None:
    conn, _ = world
    deps, messenger = bot(config, reference, conn, FakeDeck())
    assert maybe_send_summary(deps, datetime(2026, 11, 8, 9, 30))
    assert maybe_send_summary(deps, datetime(2026, 11, 15, 9, 30))
    assert messenger.pins == [("pin", 1), ("pin", 3), ("unpin", 1)]


@pytest.mark.parametrize(
    "failure",
    [
        TelegramError("pinChatMessage: not enough rights to manage pinned messages"),
        httpx.ReadTimeout("pinChatMessage timed out"),
    ],
)
def test_a_failed_pin_never_resends_the_summary(
    config: TripConfig, reference: Reference, world: World, failure: Exception
) -> None:
    conn, _ = world
    deps, messenger = bot(config, reference, conn, FakeDeck())
    messenger.pin_failure = failure
    logged: list[str] = []
    deps.log = logged.append
    sunday = datetime(2026, 11, 8, 9, 30)
    assert maybe_send_summary(deps, sunday)
    assert not maybe_send_summary(deps, sunday.replace(hour=11))
    assert [sent.kind for sent in messenger.sent] == ["album"]
    assert repo.last_summary(conn) is not None
    assert any("pinChatMessage" in line for line in logged)


def test_nightly_alerts_become_cards(
    config: TripConfig, reference: Reference, world: World
) -> None:
    conn, report = world
    deck = FakeDeck()
    deps, messenger = bot(config, reference, conn, deck)
    report.newly_failing = ["tripcom"]
    report.alerts = [Alert("record_low", "B", Money(600000, "EUR"), Money(620000, "EUR"))]
    notify_nightly(messenger, CHAT, report, deps.context(NOW), topic=6)
    assert deck.calls == ["alert B"]
    assert [(sent.kind, sent.topic) for sent in messenger.sent] == [("photo", 6), ("text", 6)]
    assert "Trip.com" in str(messenger.sent[1].body)


def test_a_source_still_down_stays_quiet(
    config: TripConfig, reference: Reference, world: World
) -> None:
    conn, report = world
    deps, messenger = bot(config, reference, conn, FakeDeck())
    report.failing_sources = ["tripcom"]
    report.newly_failing = []
    report.alerts = []
    notify_nightly(messenger, CHAT, report, deps.context(NOW), topic=None)
    assert messenger.sent == []


def test_api_sends_html_messages_and_raises_on_errors() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        api = TelegramApi(client, "TOKEN")
        api.send_message(CHAT, "<b>hi</b>", Button("Details", "https://x.example"), topic=6)
        sent = api.send_photo(CHAT, b"\x89PNG", None, Button("Details", "https://x.example"))
        with pytest.raises(TelegramError, match="Unauthorized"):
            api.username()

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/botTOKEN/sendMessage"
    assert body["parse_mode"] == "HTML"
    assert body["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://x.example"
    assert body["message_thread_id"] == 6
    assert b'name="photo"' in seen[1].content
    assert sent == 7


def test_album_attaches_each_card_and_captions_the_first() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"ok": True, "result": [{"message_id": 4}, {"message_id": 5}]}
        )

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        ids = TelegramApi(client, "TOKEN").send_album(CHAT, (b"one", b"two"), "<b>c</b>", topic=6)

    body = seen[0].content.decode("latin-1")
    media = json.loads(body.split('name="media"\r\n\r\n')[1].split("\r\n--")[0])
    assert ids == [4, 5]
    assert seen[0].url.path == "/botTOKEN/sendMediaGroup"
    assert [item["media"] for item in media] == ["attach://card0", "attach://card1"]
    assert media[0]["caption"] == "<b>c</b>" and "caption" not in media[1]
    assert 'name="card0"' in body and 'name="card1"' in body
    assert 'name="message_thread_id"\r\n\r\n6' in body


def test_get_updates_long_polls_from_the_offset() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        updates = TelegramApi(client, "TOKEN").get_updates(7)

    assert updates == [{"update_id": 7}]
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/botTOKEN/getUpdates"
    assert (body["timeout"], body["offset"], body["allowed_updates"]) == (50, 7, ["message"])
    assert seen[0].extensions["timeout"]["read"] == 60
