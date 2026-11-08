"""What a command handler can see: config, store, current rankings and the cards."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from tripbot.config import TripConfig
from tripbot.domain.alerts import Alert
from tripbot.pipeline.state import Rankings, load_rankings
from tripbot.reference import Reference
from tripbot.render.cards import CardRenderer
from tripbot.render.deck import Deck


class CardDeck(Protocol):
    def best_window(self) -> str | None: ...
    def summary(self, window_id: str | None = None) -> bytes | None: ...
    def alert(self, alert: Alert) -> bytes | None: ...
    def route(self, window_id: str, alternative_id: str | None = None) -> bytes | None: ...
    def route_map(self, window_id: str) -> bytes | None: ...
    def banner(self, window_id: str) -> bytes | None: ...
    def history_card(self, window_id: str) -> bytes | None: ...
    def windows(self) -> bytes: ...
    def other(self, window_id: str) -> bytes | None: ...


DeckOpener = Callable[["BotContext"], AbstractContextManager[CardDeck]]


@dataclass
class BotContext:
    config: TripConfig  # the plan as the group last edited it
    base: TripConfig  # `trip.yaml`, which edits apply to
    reference: Reference
    conn: sqlite3.Connection
    now: datetime
    open_deck: DeckOpener
    dashboard_url: str | None = None
    _rankings: Rankings | None = field(default=None, repr=False)

    def rankings(self) -> Rankings:
        if self._rankings is None:
            self._rankings = load_rankings(self.conn, self.config, self.reference, self.now)
        return self._rankings

    def best_window(self) -> str | None:
        bests = [(r.best.score_eur.minor, w) for w, r in self.rankings().main.items() if r.best]
        return min(bests)[1] if bests else None

    def window_arg(self, args: list[str]) -> str | None:
        """The window named in the arguments, else the current best window."""
        for arg in args:
            window = self.config.window(arg)
            if window is not None:
                return window.id
        return self.best_window()

    def use(self, config: TripConfig, rankings: Rankings | None = None) -> None:
        """Switch to another plan; without `rankings` they're worked out again on next use."""
        self.config = config
        self._rankings = rankings


@contextmanager
def chromium_deck(context: BotContext) -> Iterator[CardDeck]:
    with CardRenderer() as renderer:
        yield Deck(
            context.config,
            context.reference,
            context.conn,
            context.rankings(),
            context.now.date(),
            renderer,
        )
