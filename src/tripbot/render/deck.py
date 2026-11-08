"""Every card the bot can send, built from the store and rendered to PNG."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from tripbot.config import TripConfig
from tripbot.domain.alerts import Alert
from tripbot.domain.money import Money
from tripbot.i18n import _
from tripbot.pipeline.state import MAIN, Rankings
from tripbot.reference import Reference
from tripbot.render import details, views
from tripbot.render.cards import CardRenderer, render_html
from tripbot.render.names import Names
from tripbot.store import repo


@dataclass
class Deck:
    config: TripConfig
    reference: Reference
    conn: sqlite3.Connection
    rankings: Rankings
    today: date
    renderer: CardRenderer

    @property
    def names(self) -> Names:
        return Names(self.config, self.reference)

    @property
    def people(self) -> int:
        return sum(group.headcount for group in self.config.groups)

    def currencies(self) -> list[str]:
        codes = {self.config.origin_of(group).currency for group in self.config.groups}
        return sorted(codes - {"EUR"})

    def best_window(self) -> str | None:
        bests = [(r.best.score_eur.minor, w) for w, r in self.rankings.main.items() if r.best]
        return min(bests)[1] if bests else None

    def reference_totals(self) -> dict[str, Money]:
        """Totals to compare against: the last summary, else the previous night."""
        summary = repo.last_summary(self.conn)
        if summary is not None:
            return summary[1]
        previous: dict[str, Money] = {}
        for window in self.config.windows:
            shots = repo.snapshots(self.conn, window.id, MAIN)
            if len(shots) >= 2:
                previous[window.id] = shots[-2].total
        return previous

    def history(self) -> dict[str, list[tuple[datetime, Money]]]:
        return {
            window.id: [
                (shot.taken_at, shot.total) for shot in repo.snapshots(self.conn, window.id)
            ]
            for window in self.config.windows
        }

    def health(self) -> dict[str, tuple[int, int]]:
        run_id = repo.last_run_id(self.conn)
        return repo.source_health(self.conn, run_id) if run_id is not None else {}

    def summary(self, window_id: str | None = None) -> bytes | None:
        chosen = window_id or self.best_window()
        if chosen is None or chosen not in self.rankings.main:
            return None
        view = views.build_summary(
            views.SummaryInputs(
                names=self.names,
                rankings=self.rankings,
                window_id=chosen,
                reference_totals=self.reference_totals(),
                history={w: [m for _, m in points] for w, points in self.history().items()},
                health=self.health(),
                today=self.today,
                currencies=self.currencies(),
            )
        )
        if view is None:
            return None
        html = render_html("summary.html.j2", view=view, people=self.people, selected=chosen)
        return self.renderer.png(html)

    def alert(self, alert: Alert) -> bytes | None:
        combo = self.rankings.main[alert.window_id].best
        if combo is None:
            return None
        view = views.build_alert(
            alert, combo, self.rankings.rates, self.names, self.currencies(), self.today
        )
        return self.renderer.png(render_html("alert.html.j2", view=view, people=self.people))

    def route(self, window_id: str, alternative_id: str | None = None) -> bytes | None:
        if alternative_id is None:
            ranking, label = self.rankings.main.get(window_id), _("Main plan")
        else:
            alternative = self.config.route.alternative(alternative_id)
            ranking = self.rankings.alternatives.get(window_id, {}).get(alternative_id)
            label = alternative.label if alternative else alternative_id
        if ranking is None or ranking.best is None:
            return None
        view = details.build_route(ranking.best, self.names, label)
        return self.renderer.png(render_html("route.html.j2", view=view))

    def history_card(self, window_id: str) -> bytes | None:
        view = details.build_history(self.history(), window_id)
        if view is None:
            return None
        return self.renderer.png(render_html("history.html.j2", view=view))

    def windows(self) -> bytes:
        view = details.compare_windows(self.rankings.main)
        return self.renderer.png(render_html("compare.html.j2", view=view))

    def other(self, window_id: str) -> bytes | None:
        main = self.rankings.main.get(window_id)
        if main is None:
            return None
        alternatives = {
            alt.id: (alt.label, self.rankings.alternatives[window_id][alt.id])
            for alt in self.config.route.alternatives
            if alt.id in self.rankings.alternatives.get(window_id, {})
        }
        view = details.compare_alternatives(main, alternatives, self.names)
        return self.renderer.png(render_html("compare.html.j2", view=view))
