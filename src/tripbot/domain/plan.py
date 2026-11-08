"""The plan the group edits: who travels from where, when, and which cities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from tripbot.domain.trip import Group, Origin, Window


@dataclass(frozen=True)
class Plan:
    origins: Mapping[str, Origin]
    groups: tuple[Group, ...]
    windows: tuple[Window, ...]
    stops: tuple[str, ...]
    nights: Mapping[str, int]

    def key(self, window: Window) -> str:
        """What a window's price depends on, hashed: prices only compare under one key.

        Names don't count, so renaming someone keeps the history; headcounts do.
        """
        basis = {
            "window": [window.start.isoformat(), window.end.isoformat()],
            "groups": sorted(
                (group.id, self.origins[group.origin].airports, group.headcount)
                for group in self.groups
            ),
            "stops": sorted((stop, self.nights[stop]) for stop in self.stops),
        }
        digest = hashlib.sha1(json.dumps(basis, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()[:12]

    def keys(self) -> dict[str, str]:
        return {window.id: self.key(window) for window in self.windows}

    def to_json(self) -> dict[str, Any]:
        return {
            "origins": {
                code: {
                    "city": origin.city,
                    "airports": list(origin.airports),
                    "currency": origin.currency,
                }
                for code, origin in self.origins.items()
            },
            "groups": [
                {"id": group.id, "origin": group.origin, "people": list(group.people)}
                for group in self.groups
            ],
            "windows": [
                {"id": window.id, "start": window.start.isoformat(), "end": window.end.isoformat()}
                for window in self.windows
            ],
            "stops": list(self.stops),
            "nights": dict(self.nights),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Plan:
        """Read the plan from `trip.yaml` or from a stored edit; both use this shape."""
        return cls(
            origins={
                str(code): Origin(
                    code=str(code),
                    city=str(spec["city"]),
                    airports=tuple(str(airport) for airport in spec["airports"]),
                    currency=str(spec["currency"]),
                )
                for code, spec in raw["origins"].items()
            },
            groups=tuple(
                Group(
                    id=str(spec["id"]),
                    origin=str(spec["origin"]),
                    people=tuple(str(person) for person in spec["people"]),
                )
                for spec in raw["groups"]
            ),
            windows=tuple(
                Window(id=str(spec["id"]), start=_date(spec["start"]), end=_date(spec["end"]))
                for spec in raw["windows"]
            ),
            stops=tuple(str(stop) for stop in raw["stops"]),
            nights={str(city): int(nights) for city, nights in raw["nights"].items()},
        )


def _date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
