"""What to search tonight: every possible search key, stalest first, within budget."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from tripbot.domain.flights import Direction, Offer
from tripbot.domain.trip import Flex, Group, Origin, Window

NEVER = datetime.min


@dataclass(frozen=True)
class SearchKey:
    source: str
    group_id: str
    direction: Direction
    origins: tuple[str, ...]
    destinations: tuple[str, ...]
    travel_date: date

    def label(self) -> str:
        return (
            f"{self.source} {self.group_id} {self.direction} "
            f"{','.join(self.origins)}→{','.join(self.destinations)} {self.travel_date}"
        )


@dataclass(frozen=True)
class SearchResult:
    status: str  # ok | empty | error
    offers: tuple[Offer, ...] = ()
    error: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class SourcePlan:
    """How one source is searched: its airport shape and nightly budget."""

    name: str
    many_airports: bool
    city_codes: bool
    budget: int


@dataclass(frozen=True)
class SearchSpace:
    groups: Sequence[Group]
    origins: Mapping[str, Origin]
    windows: Sequence[Window]
    flex: Flex
    china_airports: Mapping[str, str]  # airport -> city
    city_codes: Mapping[str, str]  # city -> Trip.com code


Focus = set[tuple[str, Direction, str]]  # (group id, direction, China city)


def all_keys(source: SourcePlan, space: SearchSpace) -> list[SearchKey]:
    """Every key this source could search, in a stable order."""
    keys: list[SearchKey] = []
    for group in space.groups:
        origin = space.origins[group.origin]
        for direction in ("outbound", "return"):
            dates = _dates(space, direction)
            for china in _china_sides(source, space):
                home = (origin.code,) if source.city_codes else origin.airports
                keys.extend(
                    SearchKey(
                        source=source.name,
                        group_id=group.id,
                        direction=direction,
                        origins=home_side if direction == "outbound" else china,
                        destinations=china if direction == "outbound" else home_side,
                        travel_date=travel_date,
                    )
                    for travel_date in dates
                    for home_side in _home_sides(source, home)
                )
    return keys


def plan_tonight(
    source: SourcePlan,
    space: SearchSpace,
    last_searched: Mapping[SearchKey, datetime],
    focus: Focus,
) -> list[SearchKey]:
    """Keys in focus first, then the rest; stalest first; never more than the budget."""
    keys = all_keys(source, space)
    order = {key: index for index, key in enumerate(keys)}

    def priority(key: SearchKey) -> tuple[int, datetime, int]:
        in_focus = _touches(key, space, focus)
        return (0 if in_focus else 1, last_searched.get(key, NEVER), order[key])

    return sorted(keys, key=priority)[: source.budget]


def _dates(space: SearchSpace, direction: str) -> list[date]:
    found: set[date] = set()
    for window in space.windows:
        for variant in window.variants(space.flex):
            if direction == "outbound":
                found.update(variant.outbound_dates(space.flex))
            else:
                found.update(variant.return_dates(space.flex))
    return sorted(found)


def _china_sides(source: SourcePlan, space: SearchSpace) -> list[tuple[str, ...]]:
    if source.many_airports:
        return [tuple(space.china_airports)]
    if source.city_codes:
        cities = dict.fromkeys(space.china_airports.values())
        return [(space.city_codes[city],) for city in cities if city in space.city_codes]
    return [(airport,) for airport in space.china_airports]


def _home_sides(source: SourcePlan, home: tuple[str, ...]) -> list[tuple[str, ...]]:
    if source.many_airports or source.city_codes:
        return [home]
    return [(airport,) for airport in home]


def _touches(key: SearchKey, space: SearchSpace, focus: Focus) -> bool:
    china = key.destinations if key.direction == "outbound" else key.origins
    cities = {space.china_airports.get(code) for code in china}
    cities |= {city for city, code in space.city_codes.items() if code in china}
    return any((key.group_id, key.direction, city) in focus for city in cities if city)
