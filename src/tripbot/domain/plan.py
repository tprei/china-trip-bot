"""The plan the group edits: who travels from where, when, and which cities.

Every edit is a pure step from one plan to the next that also says what changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

from tripbot.domain.trip import Group, Origin, Window

ChangeKind = Literal[
    "person_added",
    "person_removed",
    "person_moved",
    "group_added",
    "group_removed",
    "group_rehomed",
    "origin_added",
    "window_added",
    "window_removed",
    "window_changed",
    "stop_added",
    "stop_removed",
    "nights_changed",
    "undone",
]

PlanProblem = Literal[
    "unknown_person",
    "person_exists",
    "bad_person",
    "unknown_group",
    "last_group",
    "unknown_origin",
    "origin_exists",
    "bad_airport",
    "bad_currency",
    "unknown_window",
    "window_exists",
    "bad_window_id",
    "bad_dates",
    "last_window",
    "unknown_city",
    "stop_exists",
    "not_a_stop",
    "last_stop",
    "bad_nights",
    "too_many_stops",
    "window_too_short",
    "window_in_past",
    "china_airport",
    "bad_home_name",
    "unchanged",
]

MAX_NAME = 20
MAX_HOME_NAME = 40
MAX_HOME_AIRPORTS = 4
NAME_MARKS = "'-."

MIN_WINDOW_NIGHTS = 3
MAX_WINDOW_NIGHTS = 30
MIN_STOP_NIGHTS = 1
MAX_STOP_NIGHTS = 10
MAX_STOPS = 5  # the planner tries every order of the stops; six already takes seconds
AIRPORT = re.compile(r"[A-Z]{3}")
WINDOW_ID = re.compile(r"[A-Z]")


class PlanError(ValueError):
    """An edit the plan can't take; `problem` says why, `subject` names what."""

    def __init__(self, problem: PlanProblem, subject: str) -> None:
        super().__init__(f"{problem}: {subject}")
        self.problem: PlanProblem = problem
        self.subject = subject


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    subject: str  # person, group id, origin code, window id or city id
    detail: str = ""  # group id, origin code, "start/end" dates or nights


@dataclass(frozen=True)
class Plan:
    origins: Mapping[str, Origin]
    groups: tuple[Group, ...]
    windows: tuple[Window, ...]
    stops: tuple[str, ...]
    nights: Mapping[str, int]

    def find_person(self, name: str) -> tuple[Group, str] | None:
        """The group of a traveller and their name as written, ignoring case."""
        wanted = name.casefold()
        for group in self.groups:
            for person in group.people:
                if person.casefold() == wanted:
                    return group, person
        return None

    def find_group(self, term: str) -> Group | None:
        """A group by id, someone in it, or its home when only one group flies from there."""
        wanted = term.casefold()
        by_id = next((group for group in self.groups if group.id.casefold() == wanted), None)
        if by_id is not None:
            return by_id
        found = self.find_person(term)
        if found is not None:
            return found[0]
        from_home = [group for group in self.groups if group.origin.casefold() == wanted]
        return from_home[0] if len(from_home) == 1 else None

    def find_origin(self, term: str) -> Origin | None:
        """A home by its code or one of its airports."""
        wanted = term.upper()
        for origin in self.origins.values():
            if wanted == origin.code or wanted in origin.airports:
                return origin
        return None

    def window(self, window_id: str) -> Window | None:
        wanted = window_id.upper()
        return next((window for window in self.windows if window.id == wanted), None)

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


Edit = tuple[Plan, Change]


def add_person(plan: Plan, person: str, group_term: str) -> Edit:
    name = _person_name(plan, person)
    group = _group(plan, group_term)
    return (
        _with_group(plan, replace(group, people=(*group.people, name))),
        Change("person_added", name, group.id),
    )


def remove_person(plan: Plan, person: str) -> Edit:
    group, name = _person(plan, person)
    left = tuple(p for p in group.people if p != name)
    if not left and len(plan.groups) == 1:
        raise PlanError("last_group", group.id)
    changed = _with_group(plan, replace(group, people=left)) if left else _without(plan, group)
    return changed, Change("person_removed", name, group.id)


def move_person(plan: Plan, person: str, group_term: str) -> Edit:
    group, name = _person(plan, person)
    target = _group(plan, group_term)
    if target.id == group.id:
        raise PlanError("unchanged", name)
    left = tuple(p for p in group.people if p != name)
    moved = _with_group(plan, replace(target, people=(*target.people, name)))
    changed = _with_group(moved, replace(group, people=left)) if left else _without(moved, group)
    return changed, Change("person_moved", name, target.id)


def add_group(plan: Plan, origin_term: str, people: Sequence[str]) -> Edit:
    origin = _origin(plan, origin_term)
    names: list[str] = []
    for person in people:
        name = _person_name(plan, person)
        if name.casefold() in (n.casefold() for n in names):
            raise PlanError("person_exists", name)
        names.append(name)
    if not names:
        raise PlanError("bad_person", "")
    taken = {group.id for group in plan.groups}
    base = origin.code.lower()
    group_id = next(
        candidate
        for candidate in (base, *(f"{base}{n}" for n in range(2, len(taken) + 3)))
        if candidate not in taken
    )
    group = Group(id=group_id, origin=origin.code, people=tuple(names))
    return replace(plan, groups=(*plan.groups, group)), Change("group_added", group_id, origin.code)


def remove_group(plan: Plan, group_term: str) -> Edit:
    group = _group(plan, group_term)
    if len(plan.groups) == 1:
        raise PlanError("last_group", group.id)
    return _without(plan, group), Change("group_removed", group.id, group.name)


def rehome_group(plan: Plan, group_term: str, origin_term: str) -> Edit:
    group = _group(plan, group_term)
    origin = _origin(plan, origin_term)
    if origin.code == group.origin:
        raise PlanError("unchanged", group.id)
    return (
        _with_group(plan, replace(group, origin=origin.code)),
        Change("group_rehomed", group.id, origin.code),
    )


def add_origin(
    plan: Plan,
    airports: Sequence[str],
    currency: str,
    city: str,
    currencies: Collection[str],
    china: Collection[str],
) -> Edit:
    """A new home; `china` lists the airports the trip lands at, which can't be homes."""
    codes = tuple(dict.fromkeys(airport.upper() for airport in airports))
    if not codes or len(codes) > MAX_HOME_AIRPORTS:
        raise PlanError("bad_airport", ",".join(codes))
    for code in codes:
        if not AIRPORT.fullmatch(code):
            raise PlanError("bad_airport", code)
        if code in china:
            raise PlanError("china_airport", code)
        if plan.find_origin(code) is not None:
            raise PlanError("origin_exists", code)
    money = currency.upper()
    if money not in currencies:
        raise PlanError("bad_currency", money)
    name = city.strip()
    if not name or len(name) > MAX_HOME_NAME:
        raise PlanError("bad_home_name", name[:MAX_HOME_NAME])
    origin = Origin(code=codes[0], city=name, airports=codes, currency=money)
    return (
        replace(plan, origins={**plan.origins, origin.code: origin}),
        Change("origin_added", origin.code, origin.city),
    )


def add_window(plan: Plan, window_id: str, start: date, end: date, today: date) -> Edit:
    wanted = window_id.upper()
    if not WINDOW_ID.fullmatch(wanted):
        raise PlanError("bad_window_id", window_id)
    if plan.window(wanted) is not None:
        raise PlanError("window_exists", wanted)
    window = _window(wanted, start, end, plan, today)
    return (
        replace(plan, windows=_by_start((*plan.windows, window))),
        Change("window_added", wanted, _dates(window)),
    )


def change_window(plan: Plan, window_id: str, start: date, end: date, today: date) -> Edit:
    current = _existing_window(plan, window_id)
    window = _window(current.id, start, end, plan, today)
    if window == current:
        raise PlanError("unchanged", current.id)
    others = tuple(w for w in plan.windows if w.id != current.id)
    return (
        replace(plan, windows=_by_start((*others, window))),
        Change("window_changed", current.id, _dates(window)),
    )


def remove_window(plan: Plan, window_id: str) -> Edit:
    current = _existing_window(plan, window_id)
    if len(plan.windows) == 1:
        raise PlanError("last_window", current.id)
    windows = tuple(w for w in plan.windows if w.id != current.id)
    return replace(plan, windows=windows), Change("window_removed", current.id)


def add_stop(plan: Plan, city: str, nights: int, known: Collection[str]) -> Edit:
    if city not in known:
        raise PlanError("unknown_city", city)
    if city in plan.stops:
        raise PlanError("stop_exists", city)
    if len(plan.stops) >= MAX_STOPS:
        raise PlanError("too_many_stops", str(MAX_STOPS))
    _check_nights(nights)
    short = next((w for w in plan.windows if w.nights < len(plan.stops) + 1), None)
    if short is not None:
        raise PlanError("window_too_short", short.id)
    return (
        replace(plan, stops=(*plan.stops, city), nights={**plan.nights, city: nights}),
        Change("stop_added", city, str(nights)),
    )


def remove_stop(plan: Plan, city: str) -> Edit:
    if city not in plan.stops:
        raise PlanError("not_a_stop", city)
    if len(plan.stops) == 1:
        raise PlanError("last_stop", city)
    stops = tuple(stop for stop in plan.stops if stop != city)
    nights = {stop: n for stop, n in plan.nights.items() if stop != city}
    return replace(plan, stops=stops, nights=nights), Change("stop_removed", city)


def set_nights(plan: Plan, city: str, nights: int) -> Edit:
    if city not in plan.stops:
        raise PlanError("not_a_stop", city)
    _check_nights(nights)
    if plan.nights[city] == nights:
        raise PlanError("unchanged", city)
    return (
        replace(plan, nights={**plan.nights, city: nights}),
        Change("nights_changed", city, str(nights)),
    )


def _person_name(plan: Plan, person: str) -> str:
    """One word of letters (and ' - .), at most MAX_NAME long: Ana, Luísa, D'Ávila."""
    name = person.strip()
    valid = all(ch.isalpha() or ch in NAME_MARKS for ch in name)
    if not name or len(name) > MAX_NAME or not valid:
        raise PlanError("bad_person", name[:MAX_NAME])
    if plan.find_person(name) is not None:
        raise PlanError("person_exists", name)
    return name


def _person(plan: Plan, person: str) -> tuple[Group, str]:
    found = plan.find_person(person)
    if found is None:
        raise PlanError("unknown_person", person)
    return found


def _group(plan: Plan, term: str) -> Group:
    group = plan.find_group(term)
    if group is None:
        raise PlanError("unknown_group", term)
    return group


def _origin(plan: Plan, term: str) -> Origin:
    origin = plan.find_origin(term)
    if origin is None:
        raise PlanError("unknown_origin", term.upper())
    return origin


def _with_group(plan: Plan, changed: Group) -> Plan:
    groups = tuple(changed if group.id == changed.id else group for group in plan.groups)
    return replace(plan, groups=groups)


def _without(plan: Plan, gone: Group) -> Plan:
    return replace(plan, groups=tuple(group for group in plan.groups if group.id != gone.id))


def _existing_window(plan: Plan, window_id: str) -> Window:
    window = plan.window(window_id)
    if window is None:
        raise PlanError("unknown_window", window_id.upper())
    return window


def _window(window_id: str, start: date, end: date, plan: Plan, today: date) -> Window:
    """A future window that fits every stop for a night and doesn't repeat another's dates."""
    window = Window(id=window_id, start=start, end=end)
    if start <= today:
        raise PlanError("window_in_past", window_id)
    if not MIN_WINDOW_NIGHTS <= window.nights <= MAX_WINDOW_NIGHTS:
        raise PlanError("bad_dates", window_id)
    if window.nights < len(plan.stops):
        raise PlanError("window_too_short", window_id)
    twin = next(
        (w for w in plan.windows if w.id != window_id and (w.start, w.end) == (start, end)), None
    )
    if twin is not None:
        raise PlanError("window_exists", twin.id)
    return window


def _by_start(windows: tuple[Window, ...]) -> tuple[Window, ...]:
    return tuple(sorted(windows, key=lambda window: (window.start, window.id)))


def _dates(window: Window) -> str:
    return f"{window.start.isoformat()}/{window.end.isoformat()}"


def _check_nights(nights: int) -> None:
    if not MIN_STOP_NIGHTS <= nights <= MAX_STOP_NIGHTS:
        raise PlanError("bad_nights", str(nights))


def _date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
