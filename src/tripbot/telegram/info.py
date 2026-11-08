"""Text commands: flights, cities, trains, visa, weather, reminders, todo and settings."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from tripbot.domain.flights import Offer, duration_label
from tripbot.domain.legs import LegOption
from tripbot.domain.money import Money
from tripbot.domain.schedule import train_sale_dates
from tripbot.domain.trip import Group
from tripbot.domain.weather import risk_label
from tripbot.pipeline.nightly import TARGET_KEY
from tripbot.pipeline.state import window_offers
from tripbot.render.names import Names, day_label
from tripbot.store import repo
from tripbot.telegram.commands import NO_DATA, Reply, Request, command
from tripbot.telegram.context import BotContext
from tripbot.telegram.format import bold, code, esc, lines

TODO_SEEDED_KEY = "todos_seeded"
TOP_OPTIONS = 3
TOP_COMBOS = 8


def register_info_commands() -> None:
    """Add the text commands to the registry, after the card commands."""
    for name, usage, text, handler in (
        ("flights", "/flights <name or origin> [window]", "top flights for one group", flights),
        ("cities", "/cities [window]", "entry × exit city comparison", cities),
        ("trains", "/trains [city] [city]", "trains on the route", trains),
        ("sleepers", "/sleepers", "overnight trains: times, days, cabins", sleepers),
        ("domestic", "/domestic", "domestic flights", domestic),
        ("visa", "/visa", "visa status and steps", visa),
        ("weather", "/weather [window]", "rain and typhoon risk per stop", weather),
        ("reminders", "/reminders [window]", "train sale dates and deadlines", reminders),
        ("todo", "/todo · /todo add <text> · /todo done <n>", "shared tasks", todo),
        ("alerts", "/alerts", "alert triggers", alerts),
        ("target", "/target <€>", "alert when a window gets under this total", target),
        ("people", "/people", "who flies from where", people),
        ("sources", "/sources", "last night's searches per source", sources),
    ):
        command(name, usage, text)(handler)


def seed_todos(context: BotContext) -> None:
    if repo.get_setting(context.conn, TODO_SEEDED_KEY):
        return
    for text in context.reference.todo_seed:
        repo.add_todo(context.conn, text, "bot", context.now)
    repo.set_setting(context.conn, TODO_SEEDED_KEY, "1")


def flights(context: BotContext, request: Request) -> Reply:
    group = _group_arg(context, request.args)
    if group is None:
        names = ", ".join(g.name for g in context.config.groups)
        return Reply(text=f"Who? {esc(names)} (or DUB, LON, SAO)")
    window_id = context.window_arg(request.args)
    window = context.config.window(window_id) if window_id else None
    if window is None:
        return Reply(text=NO_DATA)
    rankings = context.rankings()
    outbound = window_offers(context.config, rankings, group.id, "outbound", window)
    inbound = window_offers(context.config, rankings, group.id, "return", window)
    currency = context.config.origin_of(group).currency
    return Reply(
        text=lines(
            bold(f"{group.name} · window {window.id} · {window.label()}"),
            bold("Out"),
            *(_offer_line(o, context, currency) for o in outbound[:TOP_OPTIONS]),
            bold("Back"),
            *(_offer_line(o, context, currency) for o in inbound[:TOP_OPTIONS]),
        )
    )


def cities(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    ranking = context.rankings().main.get(window_id or "")
    if ranking is None or not ranking.combos:
        return Reply(text=NO_DATA)
    names = Names(context.config, context.reference)
    rows = [
        f"{code(f'{names.city(c.entry)} → {names.city(c.exit)}')} {esc(c.total_eur.whole())}"
        + ("" if c.within_rule else " · out of rule")
        for c in ranking.combos[:TOP_COMBOS]
    ]
    return Reply(text=lines(bold(f"Entry × exit · window {ranking.window.id}"), *rows))


def trains(context: BotContext, request: Request) -> Reply:
    wanted = [arg.lower() for arg in request.args]
    rows = [
        _leg_line(origin, destination, option, context)
        for origin, destination, option in context.reference.legs.listed()
        if option.mode != "flight" and all(w in (origin, destination) for w in wanted)
    ]
    return Reply(text=lines(bold("Trains"), *rows) if rows else "No trains between those cities.")


def sleepers(context: BotContext, request: Request) -> Reply:
    rows = [
        _leg_line(origin, destination, option, context)
        + (f" · {esc(option.cabin)}" if option.cabin else "")
        for origin, destination, option in context.reference.legs.listed()
        if option.overnight
    ]
    return Reply(text=lines(bold("Sleepers"), *rows, "Tickets open 15 days ahead."))


def domestic(context: BotContext, request: Request) -> Reply:
    rows = [
        _leg_line(origin, destination, option, context)
        for origin, destination, option in context.reference.legs.listed()
        if option.mode == "flight"
    ]
    return Reply(text=lines(bold("Domestic flights"), *rows, "Fares are estimates."))


def visa(context: BotContext, request: Request) -> Reply:
    info = context.reference.visa
    return Reply(
        text=lines(
            bold(f"{info.status} · {info.passport} passports"),
            f"Visa-free ends {esc(day_label(info.visa_free_until))} · decide by "
            f"{esc(day_label(info.decide_by))}",
            *(f"• {esc(fact)}" for fact in info.facts),
            bold("Steps"),
            *(f"{n}. {esc(step)}" for n, step in enumerate(info.steps, start=1)),
            f"<i>Checked {esc(day_label(info.as_of))}</i>",
        )
    )


def weather(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    best = context.rankings().main[window_id].best if window_id else None
    if best is None:
        return Reply(text=NO_DATA)
    names = Names(context.config, context.reference)
    rows = []
    for stay in best.route.stays:
        city = context.reference.cities.get(stay.city)
        months = Counter(night.month for night in stay.night_dates())
        for month, nights in months.items():
            risk = context.reference.climate.risk(city.region, month) if city else 0.0
            climate = context.reference.climate.month(city.region, month) if city else None
            rain = f" · {climate.rain_mm} mm" if climate else ""
            rows.append(
                f"{esc(names.city(stay.city))} · {nights} night{'s' if nights != 1 else ''} in "
                f"{datetime(2027, month, 1):%b}"
                f" · {risk_label(risk)}{rain}"
            )
    return Reply(text=lines(bold(f"Weather · window {best.window.id}"), *rows))


def reminders(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    best = context.rankings().main[window_id].best if window_id else None
    names = Names(context.config, context.reference)
    rows = [f"{esc(day_label(context.reference.visa.decide_by))} · visa decision"]
    if best is not None:
        for sale in train_sale_dates(best.route):
            leg = f"{names.city(sale.origin)} → {names.city(sale.destination)} {sale.train}"
            rows.append(
                f"{esc(day_label(sale.opens))} · tickets open: {esc(leg)} "
                f"({esc(day_label(sale.travel_day))})"
            )
    return Reply(text=lines(bold("Reminders"), *rows))


def todo(context: BotContext, request: Request) -> Reply:
    args = request.args
    if args[:1] == ["add"] and len(args) > 1:
        repo.add_todo(context.conn, " ".join(args[1:]), request.sender, context.now)
    elif args[:1] == ["done"] and len(args) == 2 and args[1].isdigit():
        items = repo.todos(context.conn)
        index = int(args[1]) - 1
        if not 0 <= index < len(items):
            return Reply(text="No task with that number.")
        repo.complete_todo(context.conn, items[index].id, request.sender, context.now)
    elif args:
        return Reply(text="Use /todo, /todo add <text> or /todo done <n>.")
    items = repo.todos(context.conn)
    rows = [f"{n}. {esc(item.text)}" for n, item in enumerate(items, start=1)]
    return Reply(text=lines(bold("To do"), *(rows or ["Nothing left."])))


def alerts(context: BotContext, request: Request) -> Reply:
    rules = context.config.alerts
    stored = repo.get_setting(context.conn, TARGET_KEY)
    goal = Money.of(stored, "EUR").whole() if stored else "not set"
    return Reply(
        text=lines(
            bold("Alerts"),
            f"New record low by €{rules.record_low_min_eur}+" if rules.record_low else "",
            f"Down {rules.drop_pct}% since the last summary",
            f"Target {esc(goal)}",
        )
    )


def target(context: BotContext, request: Request) -> Reply:
    digits = "".join(ch for ch in "".join(request.args) if ch.isdigit())
    if not digits:
        return Reply(text="Use /target 5000 (euros, whole group).")
    repo.set_setting(context.conn, TARGET_KEY, digits)
    return Reply(text=f"Target set: {esc(Money.of(digits, 'EUR').whole())}")


def people(context: BotContext, request: Request) -> Reply:
    rows = []
    for group in context.config.groups:
        origin = context.config.origin_of(group)
        rows.append(f"{code(origin.code)} {esc(group.name)} · {esc(', '.join(origin.airports))}")
    total = sum(group.headcount for group in context.config.groups)
    return Reply(text=lines(bold(f"{total} people"), *rows))


def sources(context: BotContext, request: Request) -> Reply:
    run_id = repo.last_run_id(context.conn)
    if run_id is None:
        return Reply(text="No run yet.")
    health = repo.source_health(context.conn, run_id)
    rows = [f"{esc(name)} · {ok}/{tried}" for name, (ok, tried) in sorted(health.items())]
    return Reply(text=lines(bold("Last night"), *(rows or ["No searches."])))


def _group_arg(context: BotContext, args: list[str]) -> Group | None:
    for arg in args:
        wanted = arg.lower()
        for group in context.config.groups:
            names = [person.lower() for person in group.people]
            if wanted in (group.id, group.origin.lower(), *names):
                return group
    return None


def _offer_line(offer: Offer, context: BotContext, currency: str) -> str:
    rates = context.rankings().rates
    price = rates.from_eur(rates.to_eur(offer.price), currency).whole()
    path = " → ".join((offer.origin, *offer.via, offer.destination))
    return (
        f"{esc(price)} · {esc(day_label(offer.depart.date()))} · {code(path)} · "
        f"{duration_label(offer.total_minutes)} · {esc(' + '.join(offer.airlines))}"
    )


def _leg_line(origin: str, destination: str, option: LegOption, context: BotContext) -> str:
    names = Names(context.config, context.reference)
    times = f"{option.depart}–{option.arrive}" if option.depart else duration_label(option.minutes)
    days = ""
    if option.runs_on is not None:
        labels = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        days = " · " + "/".join(labels[d] for d in sorted(option.runs_on))
    estimate = " · est." if option.estimate else ""
    return (
        f"{esc(names.city(origin))} → {esc(names.city(destination))} · {esc(option.name)} · "
        f"{esc(times)} · {esc(option.fare.whole())}{days}{estimate}"
    )
