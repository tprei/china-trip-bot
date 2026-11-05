"""Hard rules every flight must pass before it is ranked."""

from __future__ import annotations

from collections.abc import Collection

from tripbot.config import FlightRules
from tripbot.domain.flights import Offer


def rejection(offer: Offer, rules: FlightRules, china_airports: Collection[str]) -> str | None:
    """Why the offer is out, or None when it passes."""
    china_end = offer.destination if offer.direction == "outbound" else offer.origin
    if china_end not in china_airports:
        return f"{china_end} is not a mainland China airport"
    if offer.stops > rules.max_stops:
        return f"{offer.stops} stops"
    blocked = [airport for airport in offer.via if airport in rules.blocked_airports]
    if blocked:
        return f"stops in {blocked[0]}"
    long_layovers = [lay for lay in offer.layovers if lay.minutes > rules.max_layover_minutes]
    if long_layovers:
        return f"{long_layovers[0].minutes // 60}h layover in {long_layovers[0].airport}"
    if offer.self_transfer and not rules.allow_self_transfer:
        return "self-transfer on separate tickets"
    return None


def passing(
    offers: list[Offer], rules: FlightRules, china_airports: Collection[str]
) -> list[Offer]:
    return [offer for offer in offers if rejection(offer, rules, china_airports) is None]
