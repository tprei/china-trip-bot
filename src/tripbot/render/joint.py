"""Presentation for intact observed round trips; no one-way price reconstruction."""

from dataclasses import dataclass
from datetime import datetime

from tripbot import i18n
from tripbot.domain.flights import duration_label
from tripbot.domain.roundtrip import Journey, RoundTripQuote
from tripbot.i18n import _


@dataclass(frozen=True)
class JointJourney:
    label: str
    path: str
    duration: str
    times: str
    segments: tuple[str, ...]
    connections: str


@dataclass(frozen=True)
class JointCard:
    name: str
    adults: int
    total: str
    per_person: str
    observed: str
    journeys: tuple[JointJourney, ...]
    warnings: tuple[str, ...]


def journey_view(label: str, journey: Journey) -> JointJourney:
    return JointJourney(
        label,
        " → ".join((journey.origin, *journey.via, journey.destination)),
        duration_label(journey.total_minutes),
        f"{_moment(journey.depart)} → {_moment(journey.arrive)}",
        tuple(
            f"{s.flight_number} · {s.airline_name} · {s.origin} {_moment(s.depart)}"
            f" → {s.destination} {_moment(s.arrive)}"
            for s in journey.segments
        ),
        " · ".join(f"{s.airport} {duration_label(s.minutes)}" for s in journey.layovers),
    )


def _moment(value: datetime) -> str:
    return f"{i18n.short_day(value.date())} {value:%H:%M}"


def joint_card(name: str, quote: RoundTripQuote) -> JointCard:
    warnings = [
        _("Short connection: {airport} {wait}").format(
            airport=stop.airport, wait=duration_label(stop.minutes)
        )
        for journey in (quote.outbound, quote.inbound)
        for stop in journey.layovers
        if stop.minutes < 60
    ]
    if "multiple bookings" in quote.fare_notes.lower():
        warnings.append(_("Multiple bookings / tickets · protection unverified"))
    if quote.outbound.arrive.hour >= 22:
        warnings.append(
            _("Late arrival: {moment} · plan hotel").format(moment=_moment(quote.outbound.arrive))
        )
    if quote.total.minor != quote.per_person.minor * quote.request.adults:
        warnings.append(_("Rounded per-person display · use observed group total"))
    return JointCard(
        name,
        quote.request.adults,
        i18n.money(quote.total),
        i18n.money(quote.per_person),
        f"{_moment(quote.quoted_at)} UTC",
        (journey_view(_("Outbound"), quote.outbound), journey_view(_("Return"), quote.inbound)),
        tuple(warnings),
    )
