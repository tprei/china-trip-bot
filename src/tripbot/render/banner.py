"""The /banner poster: the best plan for a window, stay by stay, with a photo of each city."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from tripbot import i18n
from tripbot.domain.flights import duration_label
from tripbot.domain.legs import Mode
from tripbot.domain.money import Money
from tripbot.domain.ranking import Combo
from tripbot.domain.routes import PlannedLeg, Stay
from tripbot.domain.trip import Window
from tripbot.reference import Photo
from tripbot.render.names import Names

TONES = 5


@dataclass(frozen=True)
class BannerStay:
    kind: Literal["stay"]
    city: str
    days: str  # "Days 1–6"
    nights: str  # "5 nights"
    highlight: str
    photo: str  # data URL
    tone: int  # 0..TONES-1, same order as the summary card's stays


@dataclass(frozen=True)
class BannerLeg:
    kind: Literal["leg"]
    mode: Mode
    day: str  # "Day 6"
    text: str  # "Flight to Zhangjiajie"
    duration: str  # "2h25"


@dataclass(frozen=True)
class BannerWindow:
    dates: str
    selected: bool


@dataclass(frozen=True)
class BannerView:
    year: int
    trip_days: str  # "14 days"
    trip_nights: str  # "13 nights"
    stops: str  # "4 destinations"
    arrival: str  # "Day 1 · Arriving in Pequim"
    train_cap: str  # "No train over 8h"
    steps: tuple[BannerStay | BannerLeg, ...]
    home: str  # "Day 14 · Train to Pequim (4h40), then the flight home"
    windows: tuple[BannerWindow, ...]
    per_person: str
    credits: str


def build_banner(best: Combo, names: Names, windows: Sequence[Window]) -> BannerView:
    """Stays and the legs between them in travel order; legs after the last stay lead home."""
    window = best.window
    route = best.route
    steps: list[BannerStay | BannerLeg] = []
    legs = list(route.legs)
    for tone, stay in enumerate(route.stays):
        while legs and legs[0].destination == stay.city:
            steps.append(_leg(legs.pop(0), window.start, names))
        steps.append(_stay(stay, window.start, tone % TONES, names))
    people = sum(choice.group.headcount for choice in best.choices)
    last_day = _day(window.end, window.start)
    shown = shown_photos(best, names)
    credits = " · ".join(f"{photo.author} ({photo.license})" for _, photo in shown)
    max_hours = names.config.route.max_train_minutes // 60
    return BannerView(
        year=window.start.year,
        trip_days=i18n.ngettext("{n} day", "{n} days", last_day).format(n=last_day),
        trip_nights=i18n.ngettext("{n} night", "{n} nights", window.nights).format(n=window.nights),
        stops=i18n.ngettext("{n} destination", "{n} destinations", len(shown)).format(n=len(shown)),
        arrival=i18n._("Day 1 · Arriving in {city}").format(city=names.city(route.entry)),
        train_cap=i18n._("No train over {hours}h").format(hours=max_hours),
        steps=tuple(steps),
        home=_home(legs, last_day, names),
        windows=tuple(
            BannerWindow(dates=i18n.date_range(w.start, w.end), selected=w.id == window.id)
            for w in windows
        ),
        per_person=i18n.money(Money(best.total_eur.minor // people, "EUR")),
        credits=i18n._("Photos (cropped): {credits} · Wikimedia Commons").format(credits=credits),
    )


def shown_photos(best: Combo, names: Names) -> list[tuple[str, Photo]]:
    """Each stay's city name and photo, once each, in route order: what the poster credits."""
    cities = dict.fromkeys(stay.city for stay in best.route.stays)
    return [(names.city(city), names.reference.guide[city].photo) for city in cities]


def _stay(stay: Stay, start: date, tone: int, names: Names) -> BannerStay:
    guide = names.reference.guide[stay.city]
    first = _day(stay.arrive, start)
    encoded = base64.b64encode(guide.photo.path.read_bytes()).decode("ascii")
    return BannerStay(
        kind="stay",
        city=names.city(stay.city),
        days=i18n._("Days {first}–{last}").format(first=first, last=first + stay.nights),
        nights=i18n.ngettext("{n} night", "{n} nights", stay.nights).format(n=stay.nights),
        highlight=guide.highlight,
        photo=f"data:image/jpeg;base64,{encoded}",
        tone=tone,
    )


def _leg(leg: PlannedLeg, start: date, names: Names) -> BannerLeg:
    return BannerLeg(
        kind="leg",
        mode=leg.option.mode,
        day=i18n._("Day {n}").format(n=_day(leg.day, start)),
        text=_leg_text(leg, names),
        duration=duration_label(leg.option.minutes),
    )


def _leg_text(leg: PlannedLeg, names: Names) -> str:
    city = names.city(leg.destination)
    if leg.option.mode == "flight":
        return i18n._("Flight to {city}").format(city=city)
    if leg.option.mode == "sleeper":
        return i18n._("Sleeper train to {city}").format(city=city)
    return i18n._("Train to {city}").format(city=city)


def _home(legs: Sequence[PlannedLeg], last_day: int, names: Names) -> str:
    """The way out of China: legs from the last stay to the exit city, then the flight home."""
    if not legs:
        return i18n._("Day {n} · Flight home").format(n=last_day)
    trips = ", ".join(
        f"{_leg_text(leg, names)} ({duration_label(leg.option.minutes)})" for leg in legs
    )
    return i18n._("Day {n} · {trips}, then the flight home").format(n=last_day, trips=trips)


def _day(day: date, start: date) -> int:
    return (day - start).days + 1
