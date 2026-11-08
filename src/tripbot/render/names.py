"""Display names for airports, cities and dates."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from tripbot.config import TripConfig
from tripbot.reference import Reference


@dataclass(frozen=True)
class Names:
    config: TripConfig
    reference: Reference

    def city(self, city_id: str) -> str:
        city = self.reference.cities.get(city_id)
        return city.name if city else city_id.title()

    def find_city(self, text: str) -> str | None:
        """A city id from its id or display name, ignoring case and accents: `pequim`, `xian`."""
        wanted = _plain(text)
        return next(
            (
                city_id
                for city_id, city in self.reference.cities.items()
                if wanted in (_plain(city_id), _plain(city.name))
            ),
            None,
        )

    def airport_city(self, airport: str) -> str:
        """`PEK` → Beijing, `LHR` → London; unknown airports keep their code."""
        china = self.config.city_of(airport)
        if china:
            return self.city(china)
        for origin in self.config.origins.values():
            if airport in origin.airports:
                return origin.city
        return airport

    def airport_name(self, airport: str) -> str | None:
        """Short display name from `data/airports.yaml`: `DXB` → Dubai."""
        return self.reference.airports.get(airport)

    def entry_airport(self, city_id: str) -> str:
        """The first airport listed for a China city: beijing → PEK."""
        return next(
            (code for code, city in self.config.china_airports.items() if city == city_id), ""
        )


def day_label(day: date) -> str:
    """`Sun 29 Aug`."""
    return f"{day:%a} {day.day} {day:%b}"


def short_date(day: date) -> str:
    """`29 Aug`."""
    return f"{day.day} {day:%b}"


def clock(moment: datetime) -> str:
    return f"{moment:%H:%M}"


def initials(name: str) -> str:
    return name.strip()[:1].upper()


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if ch.isalnum())
