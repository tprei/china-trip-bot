"""Fakes for the Telegram side: a recording messenger and a deck that returns stub PNGs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from tripbot.config import Budgets, TripConfig
from tripbot.domain.alerts import Alert
from tripbot.pipeline.nightly import NightlyDeps, NightlyReport, run_nightly
from tripbot.reference import Reference
from tripbot.render.samples import SampleSource
from tripbot.store.db import connect
from tripbot.telegram.api import Button
from tripbot.telegram.bot import BotDeps
from tripbot.telegram.context import BotContext, CardDeck, DeckOpener

CHAT = "-100123"
FIRST_NIGHT = datetime(2026, 11, 7, 3, 0)

World = tuple[sqlite3.Connection, NightlyReport]


@dataclass
class Sent:
    kind: str
    body: str | bytes | tuple[bytes, ...]
    button: Button | None
    topic: int | None = None
    caption: str | None = None


@dataclass
class FakeMessenger:
    """Records what the bot sends; message ids count up per photo, like Telegram's."""

    sent: list[Sent] = field(default_factory=list)
    pins: list[tuple[str, int]] = field(default_factory=list)
    pin_failure: Exception | None = None
    next_id: int = 1

    def send_message(
        self, chat_id: str, text: str, button: Button | None = None, topic: int | None = None
    ) -> None:
        self.sent.append(Sent("text", text, button, topic))
        self.next_id += 1

    def send_photo(
        self,
        chat_id: str,
        png: bytes,
        caption: str | None = None,
        button: Button | None = None,
        topic: int | None = None,
    ) -> int:
        self.sent.append(Sent("photo", png, button, topic, caption))
        self.next_id += 1
        return self.next_id - 1

    def send_album(
        self,
        chat_id: str,
        pngs: tuple[bytes, ...],
        caption: str | None = None,
        topic: int | None = None,
    ) -> list[int]:
        self.sent.append(Sent("album", pngs, None, topic, caption))
        ids = list(range(self.next_id, self.next_id + len(pngs)))
        self.next_id += len(pngs)
        return ids

    def pin_message(self, chat_id: str, message_id: int) -> None:
        if self.pin_failure is not None:
            raise self.pin_failure
        self.pins.append(("pin", message_id))

    def unpin_message(self, chat_id: str, message_id: int) -> None:
        self.pins.append(("unpin", message_id))


@dataclass
class FakeDeck:
    calls: list[str] = field(default_factory=list)

    def best_window(self) -> str | None:
        return "B"

    def summary(self, window_id: str | None = None) -> tuple[bytes, ...] | None:
        self.calls.append(f"summary {window_id}")
        return (b"summary",)

    def alert(self, alert: Alert) -> bytes | None:
        self.calls.append(f"alert {alert.window_id}")
        return b"alert"

    def route(self, window_id: str, alternative_id: str | None = None) -> bytes | None:
        self.calls.append(f"route {window_id} {alternative_id}")
        return b"route"

    def route_map(self, window_id: str) -> bytes | None:
        self.calls.append(f"map {window_id}")
        return b"map"

    def banner(self, window_id: str) -> bytes | None:
        self.calls.append(f"banner {window_id}")
        return b"banner"

    def history_card(self, window_id: str) -> bytes | None:
        self.calls.append(f"history {window_id}")
        return None

    def windows(self) -> bytes:
        self.calls.append("windows")
        return b"windows"

    def other(self, window_id: str) -> bytes | None:
        self.calls.append(f"other {window_id}")
        return b"other"


def opener(deck: FakeDeck) -> DeckOpener:
    @contextmanager
    def open_deck(context: BotContext) -> Iterator[CardDeck]:
        yield deck

    return open_deck


def sample_world(
    config: TripConfig, reference: Reference
) -> tuple[sqlite3.Connection, NightlyReport]:
    conn = connect(":memory:")
    full = replace(config, budgets=Budgets(google=0, serpapi=1000, tripcom=0))
    report = None
    for night, discount in enumerate((0, 6)):
        deps = NightlyDeps(full, reference, conn, [SampleSource(discount)], log=lambda _: None)
        report = run_nightly(deps, FIRST_NIGHT + timedelta(days=night))
    assert report is not None
    return conn, report


def bot(
    config: TripConfig, reference: Reference, conn: sqlite3.Connection, deck: FakeDeck
) -> tuple[BotDeps, FakeMessenger]:
    messenger = FakeMessenger()
    deps = BotDeps(
        base=config,
        reference=reference,
        conn=conn,
        messenger=messenger,
        chat_id=CHAT,
        dashboard_url="https://trip.example/dash",
        open_deck=opener(deck),
        log=lambda _: None,
    )
    return deps, messenger


def message(
    text: str, chat: str = CHAT, update_id: int = 1, topic: int | None = None
) -> dict[str, object]:
    body: dict[str, object] = {"chat": {"id": int(chat)}, "from": {"first_name": "Luísa"}}
    body["text"] = text
    if topic is not None:
        body["message_thread_id"] = topic
        body["is_topic_message"] = True
    return {"update_id": update_id, "message": body}
