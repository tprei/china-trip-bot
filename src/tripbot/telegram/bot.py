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

from tripbot.config import TripConfig
from tripbot.domain.schedule import summary_due, week_key
from tripbot.reference import Reference
from tripbot.store import repo
from tripbot.telegram.api import Messenger, TelegramApi, TelegramError
from tripbot.telegram.commands import REGISTRY, Reply, Request, details_button, dispatch, parse
from tripbot.telegram.context import BotContext, DeckOpener, chromium_deck

OFFSET_KEY = "telegram_offset"
RETRY_SECONDS = 30


@dataclass
class BotDeps:
    config: TripConfig
    reference: Reference
    conn: sqlite3.Connection
    messenger: Messenger
    chat_id: str
    dashboard_url: str | None = None
    open_deck: DeckOpener = chromium_deck
    log: Callable[[str], None] = field(default=print)

    def context(self, now: datetime) -> BotContext:
        return BotContext(
            config=self.config,
            reference=self.reference,
            conn=self.conn,
            now=now,
            open_deck=self.open_deck,
            dashboard_url=self.dashboard_url,
        )

    def local_now(self) -> datetime:
        return datetime.now(ZoneInfo(self.config.timezone)).replace(tzinfo=None)


def handle_update(
    deps: BotDeps, update: dict[str, Any], username: str | None, now: datetime
) -> None:
    message = update.get("message") or {}
    if str((message.get("chat") or {}).get("id")) != deps.chat_id:
        return
    parsed = parse(str(message.get("text") or ""), username)
    if parsed is None:
        return
    name, args = parsed
    sender = str((message.get("from") or {}).get("first_name") or "someone")
    try:
        reply = dispatch(deps.context(now), Request(name=name, args=args, sender=sender))
    except Exception as error:  # one bad command must not kill the loop
        deps.log(f"/{name} failed: {type(error).__name__}: {error}")
        reply = Reply(text="Something went wrong with that command.")
    if reply is not None:
        send(deps.messenger, deps.chat_id, reply)


def send(messenger: Messenger, chat_id: str, reply: Reply) -> None:
    if reply.photo is not None:
        messenger.send_photo(chat_id, reply.photo, reply.caption, reply.button)
    elif reply.text is not None:
        messenger.send_message(chat_id, reply.text, reply.button)


def maybe_send_summary(deps: BotDeps, now_local: datetime) -> bool:
    last = repo.last_summary(deps.conn)
    if not summary_due(now_local, deps.config.summary, last[0] if last else None):
        return False
    context = deps.context(now_local)
    rankings = context.rankings()
    totals = {w: r.best.total_eur for w, r in rankings.main.items() if r.best is not None}
    if totals:
        with context.open_deck(context) as deck:
            png = deck.summary()
        if png is not None:
            deps.messenger.send_photo(deps.chat_id, png, None, details_button(context))
    repo.record_summary(deps.conn, week_key(now_local.date()), totals, now_local)
    return True


def run_forever(deps: BotDeps, api: TelegramApi) -> None:
    username = api.username()
    api.set_my_commands([(spec.name, spec.help) for spec in REGISTRY.values()])
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
