"""Text commands: flights, cities, trains, visa, weather, reminders, todo and settings."""

from __future__ import annotations

from collections import Counter
from datetime import date

from babel.dates import format_date, get_day_names

from tripbot import i18n
from tripbot.domain.flights import Offer, duration_label
from tripbot.domain.legs import LegOption
from tripbot.domain.money import Money
from tripbot.domain.schedule import train_sale_dates
from tripbot.domain.trip import Group
from tripbot.domain.weather import risk_label
from tripbot.i18n import gettext_noop
from tripbot.pipeline.nightly import TARGET_KEY
from tripbot.pipeline.state import window_offers
from tripbot.render.names import Names
from tripbot.store import repo
from tripbot.telegram.commands import NO_DATA, Reply, Request, command
from tripbot.telegram.context import BotContext
from tripbot.telegram.format import bold, code, esc, lines
from tripbot.telegram.links import links

TODO_SEEDED_KEY = "todos_seeded"
TOP_OPTIONS = 3
TOP_COMBOS = 8


def register_info_commands() -> None:
    """Add the text commands to the registry, after the card commands."""
    for name, usage, text, handler in (
        (
            gettext_noop("/flights"),
            gettext_noop("/flights <name or origin> [window]"),
            gettext_noop("top flights for one group"),
            flights,
        ),
        (
            gettext_noop("/links"),
            gettext_noop("/links [window]"),
            gettext_noop("every ticket with its link, and how the total adds up"),
            links,
        ),
        (
            gettext_noop("/cities"),
            gettext_noop("/cities [window]"),
            gettext_noop("entry × exit city comparison"),
            cities,
        ),
        (
            gettext_noop("/trains"),
            gettext_noop("/trains [city] [city]"),
            gettext_noop("trains on the route"),
            trains,
        ),
        (
            gettext_noop("/sleepers"),
            gettext_noop("/sleepers"),
            gettext_noop("overnight trains: times, days, cabins"),
            sleepers,
        ),
        (
            gettext_noop("/domestic"),
            gettext_noop("/domestic"),
            gettext_noop("domestic flights"),
            domestic,
        ),
        (
            gettext_noop("/visa"),
            gettext_noop("/visa"),
            gettext_noop("visa status and steps"),
            visa,
        ),
        (
            gettext_noop("/weather"),
            gettext_noop("/weather [window]"),
            gettext_noop("rain and typhoon risk per stop"),
            weather,
        ),
        (
            gettext_noop("/about"),
            gettext_noop("/about [city]"),
            gettext_noop("each city on the route and what to see"),
            about,
        ),
        (
            gettext_noop("/reminders"),
            gettext_noop("/reminders [window]"),
            gettext_noop("train sale dates and deadlines"),
            reminders,
        ),
        (
            gettext_noop("/todo"),
            gettext_noop("/todo · /todo add <text> · /todo done <n>"),
            gettext_noop("shared tasks"),
            todo,
        ),
        (
            gettext_noop("/alerts"),
            gettext_noop("/alerts"),
            gettext_noop("alert triggers"),
            alerts,
        ),
        (
            gettext_noop("/target"),
            gettext_noop("/target <€>"),
            gettext_noop("alert when a window gets under this total"),
            target,
        ),
        (
            gettext_noop("/people"),
            gettext_noop("/people"),
            gettext_noop("who flies from where"),
            people,
        ),
        (
            gettext_noop("/sources"),
            gettext_noop("/sources"),
            gettext_noop("last night's searches per source"),
            sources,
        ),
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
        return Reply(text=i18n._("Who? {names} (or DUB, LON, SAO)").format(names=esc(names)))
    window_id = context.window_arg(request.args)
    window = context.config.window(window_id) if window_id else None
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    rankings = context.rankings()
    outbound = window_offers(context.config, rankings, group, "outbound", window)
    inbound = window_offers(context.config, rankings, group, "return", window)
    currency = context.config.origin_of(group).currency
    return Reply(
        text=lines(
            bold(
                i18n._("{group} · window {window} · {dates}").format(
                    group=group.name,
                    window=window.id,
                    dates=i18n.date_range(window.start, window.end),
                )
            ),
            bold(i18n.pgettext("flight direction", "Out")),
            *(_offer_line(o, context, currency) for o in outbound[:TOP_OPTIONS]),
            bold(i18n.pgettext("flight direction", "Back")),
            *(_offer_line(o, context, currency) for o in inbound[:TOP_OPTIONS]),
        )
    )


def cities(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    ranking = context.rankings().main.get(window_id or "")
    if ranking is None or not ranking.combos:
        return Reply(text=i18n._(NO_DATA))
    names = Names(context.config, context.reference)
    rows = [
        f"{code(f'{names.city(c.entry)} → {names.city(c.exit)}')} {esc(i18n.money(c.total_eur))}"
        + (f" · {i18n._('out of rule')}" if not c.within_rule else "")
        for c in ranking.combos[:TOP_COMBOS]
    ]
    return Reply(
        text=lines(
            bold(i18n._("Entry × exit · window {window}").format(window=ranking.window.id)),
            *rows,
        )
    )


def trains(context: BotContext, request: Request) -> Reply:
    wanted = [arg.lower() for arg in request.args]
    rows = [
        _leg_line(origin, destination, option, context)
        for origin, destination, option in context.reference.legs.listed()
        if option.mode != "flight" and all(w in (origin, destination) for w in wanted)
    ]
    if rows:
        return Reply(text=lines(bold(i18n._("Trains")), *rows))
    return Reply(text=i18n._("No trains between those cities."))


def sleepers(context: BotContext, request: Request) -> Reply:
    rows = [
        _leg_line(origin, destination, option, context)
        + (f" · {esc(option.cabin)}" if option.cabin else "")
        for origin, destination, option in context.reference.legs.listed()
        if option.overnight
    ]
    tickets_open = i18n.ngettext(
        "Tickets open {n} day ahead.",
        "Tickets open {n} days ahead.",
        15,
    ).format(n=15)
    return Reply(text=lines(bold(i18n._("Sleepers")), *rows, tickets_open))


def domestic(context: BotContext, request: Request) -> Reply:
    rows = [
        _leg_line(origin, destination, option, context)
        for origin, destination, option in context.reference.legs.listed()
        if option.mode == "flight"
    ]
    return Reply(
        text=lines(
            bold(i18n._("Domestic flights")),
            *rows,
            i18n._("Fares are estimates."),
        )
    )


def visa(context: BotContext, request: Request) -> Reply:
    info = context.reference.visa
    return Reply(
        text=lines(
            bold(
                i18n._("{status} · {passport} passports").format(
                    status=info.status,
                    passport=info.passport,
                )
            ),
            i18n._("Visa-free ends {end_date} · decide by {decision_date}").format(
                end_date=esc(i18n.day(info.visa_free_until)),
                decision_date=esc(i18n.day(info.decide_by)),
            ),
            *(f"• {esc(fact)}" for fact in info.facts),
            bold(i18n._("Steps")),
            *(f"{n}. {esc(step)}" for n, step in enumerate(info.steps, start=1)),
            f"<i>{esc(i18n._('Checked {date}').format(date=i18n.day(info.as_of)))}</i>",
        )
    )


def weather(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    best = context.rankings().main[window_id].best if window_id else None
    if best is None:
        return Reply(text=i18n._(NO_DATA))
    names = Names(context.config, context.reference)
    rows = []
    for stay in best.route.stays:
        city = context.reference.cities.get(stay.city)
        months = Counter(night.month for night in stay.night_dates())
        for month, nights in months.items():
            risk = context.reference.climate.risk(city.region, month) if city else 0.0
            climate = context.reference.climate.month(city.region, month) if city else None
            rain = f" · {climate.rain_mm} mm" if climate else ""
            month_label = format_date(
                date(2027, month, 1),
                format="MMM",
                locale=i18n.active().locale,
            )
            nights_label = i18n.ngettext(
                "{n} night in {month}",
                "{n} nights in {month}",
                nights,
            ).format(n=nights, month=month_label)
            rows.append(f"{esc(names.city(stay.city))} · {nights_label} · {_risk_text(risk)}{rain}")
    return Reply(
        text=lines(
            bold(i18n._("Weather · window {window}").format(window=best.window.id)),
            *rows,
        )
    )


def about(context: BotContext, request: Request) -> Reply:
    names = Names(context.config, context.reference)
    window_id = context.best_window()
    best = context.rankings().main[window_id].best if window_id else None
    nights = {stay.city: stay.nights for stay in best.route.stays} if best else {}
    if request.args:
        city = names.find_city(" ".join(request.args))
        if city is None:
            known = ", ".join(sorted(names.city(city_id) for city_id in context.reference.guide))
            text = i18n._("No guide for {city}. Try one of: {cities}")
            return Reply(text=esc(text.format(city=" ".join(request.args), cities=known)))
        cities = [city]
    else:
        cities = list(best.route.order if best else context.config.route.stops)
    blocks = []
    for city in cities:
        guide = context.reference.guide[city]
        heading = bold(names.city(city))
        if city in nights:
            count = nights[city]
            heading += " · " + esc(i18n.ngettext("{n} night", "{n} nights", count).format(n=count))
        sights = [f"• {esc(sight)}" for sight in guide.sights]
        blocks.append(lines(heading, esc(guide.summary), *sights))
    return Reply(text="\n\n".join(blocks))


def reminders(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    best = context.rankings().main[window_id].best if window_id else None
    names = Names(context.config, context.reference)
    rows = [
        i18n._("{date} · visa decision").format(
            date=esc(i18n.day(context.reference.visa.decide_by))
        )
    ]
    if best is not None:
        for sale in train_sale_dates(best.route):
            leg = f"{names.city(sale.origin)} → {names.city(sale.destination)} {sale.train}"
            rows.append(
                i18n._("{opening_date} · tickets open: {leg} ({travel_date})").format(
                    opening_date=esc(i18n.day(sale.opens)),
                    leg=esc(leg),
                    travel_date=esc(i18n.day(sale.travel_day)),
                )
            )
    return Reply(text=lines(bold(i18n._("Reminders")), *rows))


def todo(context: BotContext, request: Request) -> Reply:
    args = request.args
    if args[:1] == [i18n.pgettext("todo", "add")] and len(args) > 1:
        repo.add_todo(context.conn, " ".join(args[1:]), request.sender, context.now)
    elif args[:1] == [i18n.pgettext("todo", "done")] and len(args) == 2 and args[1].isdigit():
        items = repo.todos(context.conn)
        index = int(args[1]) - 1
        if not 0 <= index < len(items):
            return Reply(text=i18n._("No task with that number."))
        repo.complete_todo(context.conn, items[index].id, request.sender, context.now)
    elif args:
        return Reply(text=esc(i18n._("Use /todo, /todo add <text> or /todo done <n>.")))
    items = repo.todos(context.conn)
    rows = [f"{n}. {esc(item.text)}" for n, item in enumerate(items, start=1)]
    return Reply(text=lines(bold(i18n._("To do")), *(rows or [i18n._("Nothing left.")])))


def alerts(context: BotContext, request: Request) -> Reply:
    rules = context.config.alerts
    stored = repo.get_setting(context.conn, TARGET_KEY)
    goal = i18n.money(Money.of(stored, "EUR")) if stored else i18n._("not set")
    return Reply(
        text=lines(
            bold(i18n._("Alerts")),
            (
                i18n._("New record low by {amount}+").format(
                    amount=esc(i18n.money(Money.of(rules.record_low_min_eur, "EUR")))
                )
                if rules.record_low
                else ""
            ),
            i18n._("Down {percent}{suffix} since the last summary").format(
                percent=rules.drop_pct, suffix="%"
            ),
            i18n._("Target {goal}").format(goal=esc(goal)),
        )
    )


def target(context: BotContext, request: Request) -> Reply:
    digits = "".join(ch for ch in "".join(request.args) if ch.isdigit())
    if not digits:
        return Reply(text=i18n._("Use /target 5000 (euros, whole group)."))
    repo.set_setting(context.conn, TARGET_KEY, digits)
    amount = esc(i18n.money(Money.of(digits, "EUR")))
    return Reply(text=i18n._("Target set: {amount}").format(amount=amount))


def people(context: BotContext, request: Request) -> Reply:
    rows = []
    for group in context.config.groups:
        origin = context.config.origin_of(group)
        rows.append(f"{code(origin.code)} {esc(group.name)} · {esc(', '.join(origin.airports))}")
    total = sum(group.headcount for group in context.config.groups)
    count = i18n.ngettext("{n} person", "{n} people", total).format(n=total)
    return Reply(text=lines(bold(count), *rows))


def sources(context: BotContext, request: Request) -> Reply:
    run_id = repo.last_run_id(context.conn)
    if run_id is None:
        return Reply(text=i18n._("No run yet."))
    health = repo.source_health(context.conn, run_id)
    rows = [f"{esc(name)} · {ok}/{tried}" for name, (ok, tried) in sorted(health.items())]
    return Reply(text=lines(bold(i18n._("Last night")), *(rows or [i18n._("No searches.")])))


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
    price = rates.from_eur(rates.to_eur(offer.price), currency)
    path = " → ".join((offer.origin, *offer.via, offer.destination))
    return (
        f"{esc(i18n.money(price))} · {esc(i18n.day(offer.depart.date()))} · {code(path)} · "
        f"{duration_label(offer.total_minutes)} · {esc(' + '.join(offer.airlines))}"
    )


def _leg_line(origin: str, destination: str, option: LegOption, context: BotContext) -> str:
    names = Names(context.config, context.reference)
    times = f"{option.depart}–{option.arrive}" if option.depart else duration_label(option.minutes)
    days = ""
    if option.runs_on is not None:
        labels = get_day_names("abbreviated", locale=i18n.active().locale)
        days = " · " + "/".join(labels[d] for d in sorted(option.runs_on))
    estimate = f" · {i18n.pgettext('fare estimate', 'est.')}" if option.estimate else ""
    return (
        f"{esc(names.city(origin))} → {esc(names.city(destination))} · "
        f"{esc(option.name)} · {esc(times)} · "
        f"{esc(i18n.money(option.fare))}{days}{estimate}"
    )


def _risk_text(risk: float) -> str:
    label = risk_label(risk)
    if label == "high":
        return i18n.pgettext("weather risk", "high")
    if label == "moderate":
        return i18n.pgettext("weather risk", "moderate")
    return i18n.pgettext("weather risk", "low")
