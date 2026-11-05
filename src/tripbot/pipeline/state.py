"""The current picture: fresh offers, rates and rankings, read from the store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from tripbot.config import TripConfig
from tripbot.domain.filters import passing
from tripbot.domain.flights import Offer
from tripbot.domain.money import Rates
from tripbot.domain.ranking import WindowRanking, rank_window
from tripbot.domain.routes import stops_without
from tripbot.domain.searches import Focus, SearchSpace
from tripbot.reference import Reference, ranking_inputs
from tripbot.store import repo

OFFER_MAX_AGE = timedelta(days=14)
MAIN = "main"


@dataclass(frozen=True)
class Rankings:
    """Per window, the main plan; per window and alternative, the /other plans."""

    main: dict[str, WindowRanking]
    alternatives: dict[str, dict[str, WindowRanking]]
    rates: Rates
    offers: tuple[Offer, ...]


def current_rates(conn: sqlite3.Connection, config: TripConfig) -> Rates:
    return repo.latest_rates(conn) or config.fallback_rates


def current_offers(conn: sqlite3.Connection, config: TripConfig, now: datetime) -> list[Offer]:
    fresh = repo.latest_offers(conn, since=now - OFFER_MAX_AGE)
    return passing(fresh, config.flights, config.china_airports)


def rank_all(
    config: TripConfig,
    reference: Reference,
    offers: list[Offer],
    rates: Rates,
    *,
    with_alternatives: bool = True,
) -> Rankings:
    inputs = ranking_inputs(config, reference, rates)
    main = {w.id: rank_window(w, offers, config.route.stops, inputs) for w in config.windows}
    alternatives: dict[str, dict[str, WindowRanking]] = {}
    if with_alternatives:
        for window in config.windows:
            alternatives[window.id] = {
                alt.id: rank_window(
                    window, offers, stops_without(config.route.stops, alt.drop), inputs
                )
                for alt in config.route.alternatives
            }
    return Rankings(main=main, alternatives=alternatives, rates=rates, offers=tuple(offers))


def load_rankings(
    conn: sqlite3.Connection, config: TripConfig, reference: Reference, now: datetime
) -> Rankings:
    rates = current_rates(conn, config)
    return rank_all(config, reference, current_offers(conn, config, now), rates)


def focus_of(rankings: Rankings) -> Focus:
    """Keep the cities of every window's best main plan fresh every night."""
    focus: Focus = set()
    for ranking in rankings.main.values():
        if ranking.best is None:
            continue
        for choice in ranking.best.choices:
            focus.add((choice.group.id, "outbound", ranking.best.entry))
            focus.add((choice.group.id, "return", ranking.best.exit))
    return focus


def search_space(config: TripConfig, reference: Reference) -> SearchSpace:
    return SearchSpace(
        groups=config.groups,
        origins=config.origins,
        windows=config.windows,
        flex=config.flex,
        china_airports=config.china_airports,
        city_codes={city: info.tripcom for city, info in reference.cities.items() if info.tripcom},
    )
