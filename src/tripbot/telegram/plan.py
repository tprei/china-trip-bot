"""Edit the plan from the chat: people, homes, windows and stops, and undo."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from tripbot import i18n
from tripbot.config import TripConfig
from tripbot.domain import plan as edits
from tripbot.domain.money import Money
from tripbot.domain.plan import Change, ChangeKind, Edit, Plan, PlanError
from tripbot.i18n import gettext_noop, pgettext
from tripbot.pipeline.state import Rankings
from tripbot.render.names import Names
from tripbot.store import plans
from tripbot.telegram.commands import Reply, Request, command
from tripbot.telegram.context import BotContext
from tripbot.telegram.format import bold, code, esc, lines

RECENT_CHANGES = 3
NIGHTS = re.compile(r"[0-9]{1,2}")
DAY = re.compile(r"(?P<day>[0-9]{1,2})/(?P<month>[0-9]{1,2})(?:/(?P<year>[0-9]{4}))?")
NEEDS_SEARCH: frozenset[ChangeKind] = frozenset(
    {"group_added", "group_rehomed", "window_added", "window_changed"}
)

PERSON_USAGE = gettext_noop("/person add <name> <group> · remove <name> · move <name> <group>")
GROUP_USAGE = gettext_noop("/group add <home> <names> · remove <group> · home <group> <home>")
HOME_USAGE = gettext_noop("/home add <airports> <currency> <city>")
WINDOW_USAGE = gettext_noop(
    "/window add <letter> <dd/mm> <dd/mm> · change <letter> <dd/mm> <dd/mm> · remove <letter>"
)
STOP_USAGE = gettext_noop("/destination add <city> <nights> · remove <city> · nights <city> <n>")

Handler = Callable[[BotContext, Request], Reply]


def register_plan_commands() -> None:
    """Add the plan commands to the registry, after the text commands."""
    for name, usage, text, handler in (
        (
            gettext_noop("/plan"),
            gettext_noop("/plan"),
            gettext_noop("who, from where, when and which cities; how to change it"),
            plan_command,
        ),
        (gettext_noop("/person"), PERSON_USAGE, gettext_noop("change who travels"), person),
        (gettext_noop("/group"), GROUP_USAGE, gettext_noop("travel groups and their home"), group),
        (gettext_noop("/home"), HOME_USAGE, gettext_noop("add a home city"), home),
        (gettext_noop("/window"), WINDOW_USAGE, gettext_noop("date windows in China"), window),
        (
            gettext_noop("/destination"),
            STOP_USAGE,
            gettext_noop("cities on the route and nights in each"),
            destination,
        ),
        (gettext_noop("/undo"), gettext_noop("/undo"), gettext_noop("undo a plan change"), undo),
    ):
        command(name, usage, text)(handler)


def plan_command(context: BotContext, request: Request) -> Reply:
    config = context.config
    names = Names(config, context.reference)
    groups = [
        f"{code(group.id)} {esc(group.name)} · {esc(_home(config.plan, group.origin))}"
        for group in config.groups
    ]
    windows = [
        f"{code(w.id)} {esc(i18n.date_range(w.start, w.end))} · "
        + esc(i18n.ngettext("{n} night", "{n} nights", w.nights).format(n=w.nights))
        for w in config.windows
    ]
    stops = " · ".join(
        f"{names.city(stop)} {config.route.nights[stop]}" for stop in config.route.stops
    )
    changes = [
        f"{esc(i18n.day(change.changed_at.date()))} · {esc(_described(context, change))}"
        for change in plans.recent_changes(context.conn, RECENT_CHANGES)
    ]
    commands = " · ".join(
        i18n._(name) for name in ("/person", "/group", "/home", "/window", "/destination", "/undo")
    )
    recent = [bold(i18n._("Last changes")), *changes, ""] if changes else []
    rules = _date_rules(config)
    return Reply(
        text="\n".join(
            [
                bold(i18n._("Groups")),
                *groups,
                "",
                bold(i18n._("Windows")),
                *windows,
                *rules,
                "",
                bold(i18n._("Route · nights per city")),
                esc(stops),
                esc(i18n._("The bot picks the order and stretches stays to fill each window.")),
                "",
                *recent,
                bold(i18n._("Change it")),
                esc(commands),
            ]
        )
    )


def _date_rules(config: TripConfig) -> list[str]:
    """How the bot may move each window, and which return days it prefers."""
    flex = config.flex
    rules: list[str] = []
    if flex.shift_days > 0:
        shift = i18n.ngettext(
            "Each window can also start {n} day earlier or later.",
            "Each window can also start {n} days earlier or later.",
            flex.shift_days,
        )
        rules.append(esc(shift.format(n=flex.shift_days)))
    if flex.weekday_return_eur > 0:
        amount = i18n.money(Money.of(flex.weekday_return_eur, "EUR"))
        weekday = i18n._(
            "A return leaving China Monday to Thursday counts {amount} more per person"
            " in the ranking, not in the total."
        )
        rules.append(esc(weekday.format(amount=amount)))
    return rules


def person(context: BotContext, request: Request) -> Reply:
    verb, args = _verb(request.args, ("add", "remove", "move"))
    current = context.config.plan
    if verb == "add" and len(args) == 2:
        return _apply(context, request, lambda: edits.add_person(current, args[0], args[1]))
    if verb == "remove" and len(args) == 1:
        return _apply(context, request, lambda: edits.remove_person(current, args[0]))
    if verb == "move" and len(args) == 2:
        return _apply(context, request, lambda: edits.move_person(current, args[0], args[1]))
    return _usage(PERSON_USAGE)


def group(context: BotContext, request: Request) -> Reply:
    verb, args = _verb(request.args, ("add", "remove", "home"))
    current = context.config.plan
    if verb == "add" and len(args) >= 2:
        return _apply(context, request, lambda: edits.add_group(current, args[0], args[1:]))
    if verb == "remove" and len(args) == 1:
        return _apply(context, request, lambda: edits.remove_group(current, args[0]))
    if verb == "home" and len(args) == 2:
        return _apply(context, request, lambda: edits.rehome_group(current, args[0], args[1]))
    return _usage(GROUP_USAGE)


def home(context: BotContext, request: Request) -> Reply:
    verb, args = _verb(request.args, ("add",))
    airports = [airport for airport in args[0].split(",") if airport] if args else []
    if verb != "add" or len(args) < 3 or not airports:
        return _usage(HOME_USAGE)
    config = context.config
    currencies = {*config.fallback_rates.per_eur, config.home_currency}
    china = set(config.china_airports)
    city = " ".join(args[2:])
    return _apply(
        context,
        request,
        lambda: edits.add_origin(config.plan, airports, args[1], city, currencies, china),
    )


def window(context: BotContext, request: Request) -> Reply:
    verb, args = _verb(request.args, ("add", "change", "remove"))
    current = context.config.plan
    if verb == "remove" and len(args) == 1:
        return _apply(context, request, lambda: edits.remove_window(current, args[0]))
    if verb in ("add", "change") and len(args) == 3:
        today = context.now.date()
        start = _day(args[1], today)
        end = _day(args[2], start) if start is not None else None
        if start is not None and end is not None:
            make = edits.add_window if verb == "add" else edits.change_window
            return _apply(context, request, lambda: make(current, args[0], start, end, today))
    return _usage(WINDOW_USAGE)


def destination(context: BotContext, request: Request) -> Reply:
    verb, args = _verb(request.args, ("add", "remove", "nights"))
    current = context.config.plan
    if verb == "remove" and args:
        city = _city(context, args)
        return _apply(context, request, lambda: edits.remove_stop(current, city))
    if verb in ("add", "nights") and len(args) >= 2 and NIGHTS.fullmatch(args[-1]):
        city, nights = _city(context, args[:-1]), int(args[-1])
        if verb == "nights":
            return _apply(context, request, lambda: edits.set_nights(current, city, nights))
        known = context.reference.cities.keys()
        return _apply(context, request, lambda: edits.add_stop(current, city, nights, known))
    return _usage(STOP_USAGE)


def undo(context: BotContext, request: Request) -> Reply:
    before = context.rankings()
    undone = plans.undo(context.conn, context.base, request.sender, context.now)
    if undone is None:
        return Reply(text=esc(i18n._("Nothing to undo.")))
    target, config = undone
    context.use(config)
    what = describe(target.change, target.changed_by, target.plan, Names(config, context.reference))
    return Reply(
        text=lines(
            esc(i18n._("{name} undid: {change}").format(name=request.sender, change=what)),
            esc(_totals(config, before, context.rankings())),
        )
    )


SENTENCES: dict[ChangeKind, str] = {
    "person_added": gettext_noop("{name} added {person} to the group from {home}."),
    "person_removed": gettext_noop("{name} took {person} off the trip."),
    "person_moved": gettext_noop("{name} moved {person} to the group from {home}."),
    "group_added": gettext_noop("{name} added {people}, flying from {home}."),
    "group_removed": gettext_noop("{name} took {people} off the trip."),
    "group_rehomed": gettext_noop("{name} changed where {people} fly from: {home}."),
    "origin_added": gettext_noop("{name} added {home} as a home."),
    "window_added": gettext_noop("{name} added window {window}: {dates}"),
    "window_changed": gettext_noop("{name} moved window {window} to {dates}"),
    "window_removed": gettext_noop("{name} removed window {window}."),
    "stop_removed": gettext_noop("{name} took {city} off the route."),
    "undone": gettext_noop("{name} undid a change."),
}


def describe(change: Change, by: str, plan: Plan, names: Names) -> str:
    """One sentence for a change, in the group's language."""
    subject, detail = change.subject, change.detail
    nights = int(detail) if NIGHTS.fullmatch(detail) else 0
    dated = change.kind in ("window_added", "window_changed")
    values = {
        "name": by,
        "person": subject,
        "people": detail if change.kind == "group_removed" else _people(plan, subject),
        "home": _home_of(change, plan),
        "window": subject,
        "dates": _dates(detail) if dated else "",
        "city": names.city(subject),
        "n": str(nights),
    }
    if change.kind == "stop_added":
        return i18n.ngettext(
            "{name} added {city} with {n} night.", "{name} added {city} with {n} nights.", nights
        ).format(**values)
    if change.kind == "nights_changed":
        return i18n.ngettext(
            "{name} set {city} to {n} night.", "{name} set {city} to {n} nights.", nights
        ).format(**values)
    return i18n._(SENTENCES[change.kind]).format(**values)


def _home_of(change: Change, plan: Plan) -> str:
    if change.kind in ("person_added", "person_moved"):
        return _group_home(plan, change.detail)
    if change.kind == "origin_added":
        return _home(plan, change.subject)
    return _home(plan, change.detail)


def problem_text(error: PlanError, context: BotContext) -> str:
    names = Names(context.config, context.reference)
    subject = error.subject
    messages = {
        "unknown_person": gettext_noop("Nobody called {subject} is on the trip."),
        "person_exists": gettext_noop("{subject} is already on the trip."),
        "bad_person": gettext_noop("A name is one word of letters, up to 20, like Ana."),
        "unknown_group": gettext_noop(
            "No group matches {subject}. Use the group code from /plan, its home or a name in it."
        ),
        "last_group": gettext_noop("The trip needs at least one person."),
        "unknown_origin": gettext_noop(
            "{subject} isn't a home yet. Add it first: /home add {subject} EUR City"
        ),
        "origin_exists": gettext_noop("{subject} is already a home airport."),
        "bad_airport": gettext_noop("{subject} isn't an airport code (three letters, like LIS)."),
        "unknown_window": gettext_noop("There's no window {subject}."),
        "window_exists": gettext_noop("Window {subject} already exists."),
        "bad_window_id": gettext_noop("A window is one letter, like D."),
        "bad_dates": gettext_noop("A window runs 3 to 30 nights, first day before the last."),
        "last_window": gettext_noop("The plan needs at least one window."),
        "stop_exists": gettext_noop("{subject} is already on the route."),
        "not_a_stop": gettext_noop("{subject} isn't on the route."),
        "last_stop": gettext_noop("The route needs at least one city."),
        "bad_nights": gettext_noop("A city gets 1 to 10 nights."),
        "too_many_stops": gettext_noop("The route takes at most {subject} cities."),
        "window_too_short": gettext_noop(
            "Window {subject} is too short: every city on the route needs a night."
        ),
        "window_in_past": gettext_noop("Window {subject} would start before tomorrow."),
        "china_airport": gettext_noop("{subject} is in China; a home is where people fly from."),
        "bad_home_name": gettext_noop("A home city name has 1 to 40 characters."),
        "unchanged": gettext_noop("Nothing to change."),
    }
    if error.problem == "bad_currency":
        currencies = sorted({*context.config.fallback_rates.per_eur, context.config.home_currency})
        text = i18n._("The bot converts {currencies}; {subject} isn't one of them.")
        return text.format(currencies=", ".join(currencies), subject=subject)
    if error.problem == "unknown_city":
        known = sorted(names.city(city) for city in context.reference.cities)
        text = i18n._("The bot has no trains or flights for {subject} yet. It knows: {cities}.")
        return text.format(subject=subject, cities=", ".join(known))
    shown = names.city(subject) if subject in context.reference.cities else subject
    return i18n._(messages[error.problem]).format(subject=shown)


def _apply(context: BotContext, request: Request, make: Callable[[], Edit]) -> Reply:
    try:
        edit = make()
    except PlanError as error:
        return Reply(text=esc(problem_text(error, context)))
    before = context.rankings()
    config = plans.save_change(context.conn, context.base, edit, request.sender, context.now)
    context.use(config)
    plan, change = edit
    names = Names(config, context.reference)
    notes = [i18n._("New flights get searched tonight.")] if change.kind in NEEDS_SEARCH else []
    undo_hint = i18n._("{command} to go back.").format(command=i18n._("/undo"))
    return Reply(
        text=lines(
            esc(describe(change, request.sender, plan, names)),
            esc(_totals(config, before, context.rankings())),
            *(esc(note) for note in notes),
            esc(undo_hint),
        )
    )


def _totals(config: TripConfig, before: Rankings, after: Rankings) -> str:
    parts = []
    for w in config.windows:
        best = after.main[w.id].best
        if best is None:
            parts.append(i18n._("{window} no complete combo").format(window=w.id))
            continue
        part = f"{w.id} {i18n.money(best.total_eur)}"
        earlier = before.main.get(w.id)
        if earlier is not None and earlier.best is not None:
            change = best.total_eur.minor - earlier.best.total_eur.minor
            if change:
                sign = "+" if change > 0 else "−"
                part += f" ({sign}{i18n.money(Money(abs(change), 'EUR'))})"
        parts.append(part)
    return i18n._("Group total now: {totals}").format(totals=" · ".join(parts))


def _verb(args: list[str], verbs: tuple[str, ...]) -> tuple[str | None, list[str]]:
    if not args:
        return None, []
    wanted = args[0].casefold()
    words = _verb_words()
    for verb in verbs:
        if words[verb].casefold() == wanted:
            return verb, args[1:]
    return None, args[1:]


def _verb_words() -> dict[str, str]:
    """The edit verbs as the group types them, spelled out so the catalog can see them."""
    return {
        "add": pgettext("plan edit", "add"),
        "remove": pgettext("plan edit", "remove"),
        "move": pgettext("plan edit", "move"),
        "home": pgettext("plan edit", "home"),
        "change": pgettext("plan edit", "change"),
        "nights": pgettext("plan edit", "nights"),
    }


def _usage(usage: str) -> Reply:
    return Reply(text=esc(i18n._("Use {usage}").format(usage=i18n._(usage))))


def _city(context: BotContext, words: list[str]) -> str:
    """A city id from its id or display name; what was typed when nothing matches."""
    typed = " ".join(words)
    return Names(context.config, context.reference).find_city(typed) or typed


def _day(text: str, after: date) -> date | None:
    """`dd/mm`, the first such day after `after`, or `dd/mm/yyyy`."""
    match = DAY.fullmatch(text)
    if match is None:
        return None
    day, month = int(match["day"]), int(match["month"])
    if match["year"] is not None:
        return _date_or_none(int(match["year"]), month, day)
    for year in range(after.year, after.year + 5):
        found = _date_or_none(year, month, day)
        if found is not None and found > after:
            return found
    return None


def _date_or_none(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _home(plan: Plan, origin_code: str) -> str:
    origin = plan.origins.get(origin_code)
    if origin is None:
        return origin_code
    return f"{origin.city} ({', '.join(origin.airports)})"


def _group_home(plan: Plan, group_id: str) -> str:
    found = next((g for g in plan.groups if g.id == group_id), None)
    if found is None or found.origin not in plan.origins:
        return group_id
    return plan.origins[found.origin].city


def _people(plan: Plan, group_id: str) -> str:
    found = next((g for g in plan.groups if g.id == group_id), None)
    return found.name if found else group_id


def _dates(detail: str) -> str:
    start, _, end = detail.partition("/")
    return i18n.date_range(date.fromisoformat(start), date.fromisoformat(end))


def _described(context: BotContext, change: plans.PlanChange) -> str:
    names = Names(context.config, context.reference)
    if change.change.kind != "undone":
        return describe(change.change, change.changed_by, change.plan, names)
    target = plans.plan_change(context.conn, int(change.change.subject))
    if target is None:
        return describe(change.change, change.changed_by, change.plan, names)
    what = describe(target.change, target.changed_by, target.plan, names)
    return i18n._("{name} undid: {change}").format(name=change.changed_by, change=what)
