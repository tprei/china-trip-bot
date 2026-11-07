"""Presentation for intact observed round trips; no one-way price reconstruction."""

from dataclasses import dataclass

from tripbot.domain.flights import duration_label
from tripbot.domain.roundtrip import Journey, RoundTripQuote


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
        f"{journey.depart:%d %b %Y %H:%M} → {journey.arrive:%d %b %Y %H:%M}",
        tuple(
            f"{s.flight_number} · {s.airline_name} · {s.origin} {s.depart:%d %b %H:%M}"
            f" → {s.destination} {s.arrive:%d %b %H:%M}"
            for s in journey.segments
        ),
        " · ".join(f"{s.airport} {duration_label(s.minutes)}" for s in journey.layovers),
    )


def joint_card(name: str, quote: RoundTripQuote) -> JointCard:
    warnings = [
        f"Short connection: {stop.airport} {duration_label(stop.minutes)}"
        for journey in (quote.outbound, quote.inbound)
        for stop in journey.layovers
        if stop.minutes < 60
    ]
    if "multiple bookings" in quote.fare_notes.lower():
        warnings.append("Multiple bookings / tickets · protection unverified")
    if quote.outbound.arrive.hour >= 22:
        warnings.append(f"Late arrival: {quote.outbound.arrive:%d %b %Y %H:%M} · plan hotel")
    if quote.total.minor != quote.per_person.minor * quote.request.adults:
        warnings.append("Rounded per-person display · use observed group total")
    return JointCard(
        name,
        quote.request.adults,
        quote.total.whole(),
        quote.per_person.whole(),
        f"{quote.quoted_at:%d %b %Y %H:%M} UTC",
        (journey_view("Outbound", quote.outbound), journey_view("Return", quote.inbound)),
        tuple(warnings),
    )
