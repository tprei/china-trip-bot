"""Build `site/index.html` from the store: every window, group, flight and plan on one page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tripbot.domain.flights import Offer
from tripbot.domain.schedule import train_sale_dates
from tripbot.domain.trip import Group, Window
from tripbot.domain.visa import VisaInfo
from tripbot.pipeline.state import window_offers
from tripbot.render import details, views
from tripbot.render.cards import render_html
from tripbot.render.deck import Deck
from tripbot.render.names import day_label
from tripbot.store import repo

TOP_FLIGHTS = 3
TOP_COMBOS = 8


@dataclass(frozen=True)
class PricedFlight:
    price: str
    line: views.FlightLine


@dataclass(frozen=True)
class GroupFlights:
    name: str
    chosen: views.GroupRow | None
    outbound: tuple[PricedFlight, ...]
    inbound: tuple[PricedFlight, ...]


@dataclass(frozen=True)
class ComboLine:
    pair: str
    total: str
    within_rule: bool
    best: bool


@dataclass(frozen=True)
class WindowPanel:
    id: str
    dates: str
    total: str | None
    others: tuple[views.MoneyLine, ...]
    groups: tuple[GroupFlights, ...]
    combos: tuple[ComboLine, ...]
    route: details.RouteView | None
    alternatives: details.CompareView
    reminders: tuple[str, ...]


@dataclass(frozen=True)
class DashboardView:
    updated: str
    selected: str
    panels: tuple[WindowPanel, ...]
    windows: details.CompareView
    history: details.HistoryView | None
    visa: VisaInfo
    todos: tuple[str, ...]


def build_dashboard(deck: Deck, now: datetime) -> DashboardView:
    """`deck` supplies the same numbers the cards use; its renderer is never called."""
    selected = deck.best_window() or deck.config.windows[0].id
    return DashboardView(
        updated=f"{day_label(now.date())} · {now:%H:%M}",
        selected=selected,
        panels=tuple(_panel(deck, window) for window in deck.config.windows),
        windows=details.compare_windows(deck.rankings.main),
        history=details.build_history(deck.history(), selected),
        visa=deck.reference.visa,
        todos=tuple(item.text for item in repo.todos(deck.conn)),
    )


def publish(deck: Deck, now: datetime, site_dir: Path) -> Path:
    return write_site(build_dashboard(deck, now), site_dir)


def write_site(view: DashboardView, site_dir: Path) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    path = site_dir / "index.html"
    path.write_text(render_html("dashboard.html.j2", view=view), encoding="utf-8")
    return path


def _panel(deck: Deck, window: Window) -> WindowPanel:
    ranking = deck.rankings.main[window.id]
    best = ranking.best
    names = deck.names
    alternatives = {
        alt.id: (alt.label, deck.rankings.alternatives[window.id][alt.id])
        for alt in deck.config.route.alternatives
        if alt.id in deck.rankings.alternatives.get(window.id, {})
    }
    sales = train_sale_dates(best.route) if best else []
    return WindowPanel(
        id=window.id,
        dates=window.label(),
        total=best.total_eur.whole() if best else None,
        others=(
            views.other_currencies(best.total_eur, deck.rankings.rates, deck.currencies())
            if best
            else ()
        ),
        groups=tuple(_group(deck, window, group) for group in deck.config.groups),
        combos=tuple(
            ComboLine(
                pair=f"{names.city(c.entry)} → {names.city(c.exit)}",
                total=c.total_eur.whole(),
                within_rule=c.within_rule,
                best=c is best,
            )
            for c in ranking.combos[:TOP_COMBOS]
        ),
        route=details.build_route(best, names, "Main plan") if best else None,
        alternatives=details.compare_alternatives(ranking, alternatives, names),
        reminders=tuple(
            f"{day_label(sale.opens)} · {names.city(sale.origin)} → "
            f"{names.city(sale.destination)} · {sale.train}"
            for sale in sales
        ),
    )


def _group(deck: Deck, window: Window, group: Group) -> GroupFlights:
    group_id = group.id
    currency = deck.config.origin_of(group).currency
    rates = deck.rankings.rates

    def priced(offers: list[Offer]) -> tuple[PricedFlight, ...]:
        return tuple(
            PricedFlight(
                price=rates.from_eur(rates.to_eur(offer.price), currency).whole(),
                line=views.flight_line(offer, deck.names),
            )
            for offer in offers[:TOP_FLIGHTS]
        )

    best = deck.rankings.main[window.id].best
    choice = best.choice(group_id) if best else None
    return GroupFlights(
        name=group.name,
        chosen=views.group_row(choice, rates, deck.names) if choice else None,
        outbound=priced(window_offers(deck.config, deck.rankings, group_id, "outbound", window)),
        inbound=priced(window_offers(deck.config, deck.rankings, group_id, "return", window)),
    )
