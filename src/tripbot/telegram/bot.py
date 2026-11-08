"""The long-polling loop: answer commands in the group and send the weekly summary."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from tripbot import i18n
from tripbot.config import TripConfig
from tripbot.domain.money import Money
from tripbot.domain.schedule import summary_due, week_key
from tripbot.reference import Reference
from tripbot.store import repo
from tripbot.store.plans import effective_config
from tripbot.telegram.api import Messenger, TelegramApi, TelegramError
from tripbot.telegram.commands import REGISTRY, Reply, Request, dispatch, parse, summary_album
from tripbot.telegram.context import BotContext, DeckOpener, chromium_deck
from tripbot.telegram.format import esc
from tripbot.telegram.info import register_info_commands, seed_todos
from tripbot.telegram.plan import register_plan_commands

OFFSET_KEY = "telegram_offset"
PINNED_KEY = "pinned_summary"
RETRY_SECONDS = 30


@dataclass
class BotDeps:
    base: TripConfig  # `trip.yaml`; each update applies the group's latest plan to it
    reference: Reference
    conn: sqlite3.Connection
    messenger: Messenger
    chat_id: str
    topic: int | None = None
    dashboard_url: str | None = None
    open_deck: DeckOpener = chromium_deck
    log: Callable[[str], None] = field(default=print)

    def __post_init__(self) -> None:
        register_info_commands()
        register_plan_commands()

    def context(self, now: datetime) -> BotContext:
        return BotContext(
            config=effective_config(self.conn, self.base),
            base=self.base,
            reference=self.reference,
            conn=self.conn,
            now=now,
            open_deck=self.open_deck,
            dashboard_url=self.dashboard_url,
        )

    def local_now(self) -> datetime:
        return datetime.now(ZoneInfo(self.base.timezone)).replace(tzinfo=None)


def handle_update(
    deps: BotDeps, update: dict[str, Any], username: str | None, now: datetime
) -> None:
    message = update.get("message") or {}
    if str((message.get("chat") or {}).get("id")) != deps.chat_id:
        return
    topic: int | None = (
        message.get("message_thread_id") if message.get("is_topic_message") else None
    )
    parsed = parse(str(message.get("text") or ""), username)
    if parsed is None:
        return
    name, args = parsed
    sender = str((message.get("from") or {}).get("first_name") or "someone")
    try:
        reply = dispatch(deps.context(now), Request(name=name, args=args, sender=sender))
    except Exception as error:  # one bad command must not kill the loop
        deps.log(f"/{name} failed: {type(error).__name__}: {error}")
        reply = Reply(text=i18n._("Something went wrong with that command."))
    if reply is None:
        return
    if not reply.pin:
        send(deps.messenger, deps.chat_id, reply, topic)
        return
    message_id = send(deps.messenger, deps.chat_id, reply, deps.topic)
    pinned = message_id is not None and pin_summary(deps, message_id)
    if pinned and topic == deps.topic:
        return
    if pinned:
        note = i18n._("Summary posted and pinned in the bot's topic.")
    else:
        note = i18n._("Summary posted in the bot's topic, but it couldn't be pinned.")
    send(deps.messenger, deps.chat_id, Reply(text=esc(note)), topic)


def send(messenger: Messenger, chat_id: str, reply: Reply, topic: int | None) -> int | None:
    """Post the reply; the id of its first card, if it has one, so it can be pinned."""
    if reply.album:
        return messenger.send_album(chat_id, reply.album, reply.caption, topic)[0]
    if reply.photo is not None:
        return messenger.send_photo(chat_id, reply.photo, reply.caption, reply.button, topic)
    if reply.text is not None:
        messenger.send_message(chat_id, reply.text, reply.button, topic)
    return None


def maybe_send_summary(deps: BotDeps, now_local: datetime) -> bool:
    last = repo.last_summary(deps.conn)
    if not summary_due(now_local, deps.base.summary, last.week if last else None):
        return False
    context = deps.context(now_local)
    totals, message_id = post_summary(deps, context)
    keys = context.config.plan.keys()
    repo.record_summary(deps.conn, week_key(now_local.date()), totals, keys, now_local)
    if message_id is not None:
        pin_summary(deps, message_id)
    return True


def post_summary(deps: BotDeps, context: BotContext) -> tuple[dict[str, Money], int | None]:
    """Post the summary album to the bot's topic: the totals it showed and its first message."""
    rankings = context.rankings()
    totals = {w: r.best.total_eur for w, r in rankings.main.items() if r.best is not None}
    if not totals:
        return totals, None
    with context.open_deck(context) as deck:
        reply = summary_album(context, deck)
    if not reply.album and reply.photo is None:
        return totals, None
    return totals, send(deps.messenger, deps.chat_id, reply, deps.topic)


def pin_summary(deps: BotDeps, message_id: int) -> bool:
    """Pin the new summary and unpin only the one the bot pinned before; other pins stay.

    False when Telegram refused the pin. A failed unpin of the old one is only logged.
    """
    previous = repo.get_setting(deps.conn, PINNED_KEY)
    try:
        deps.messenger.pin_message(deps.chat_id, message_id)
    except (TelegramError, httpx.HTTPError) as error:
        deps.log(f"pinning the summary failed: {error}")
        return False
    repo.set_setting(deps.conn, PINNED_KEY, str(message_id))
    if previous is not None:
        try:
            deps.messenger.unpin_message(deps.chat_id, int(previous))
        except (TelegramError, httpx.HTTPError) as error:
            deps.log(f"unpinning the previous summary failed: {error}")
    return True


def run_forever(deps: BotDeps, api: TelegramApi) -> None:
    username = api.username()
    seed_todos(deps.context(deps.local_now()))
    api.set_my_commands(
        [
            (
                i18n._(spec.name).removeprefix("/"),
                f"{i18n._(spec.usage)} — {i18n._(spec.help)}",
            )
            for spec in REGISTRY.values()
        ]
    )
    deps.log(f"polling as @{username}")
    while True:
        try:
            poll_once(deps, api, username)
            maybe_send_summary(deps, deps.local_now())
        except (httpx.HTTPError, TelegramError) as error:
            deps.log(f"telegram unavailable: {error}; retrying in {RETRY_SECONDS}s")
            time.sleep(RETRY_SECONDS)


def poll_once(deps: BotDeps, api: TelegramApi, username: str | None) -> None:
    stored = repo.get_setting(deps.conn, OFFSET_KEY)
    updates = api.get_updates(int(stored) if stored else None)
    for update in updates:
        handle_update(deps, update, username, deps.local_now())
        repo.set_setting(deps.conn, OFFSET_KEY, str(int(update["update_id"]) + 1))
