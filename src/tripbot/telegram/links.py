"""/links: every ticket in the chosen plan, one line each, and the sum that makes the total."""

from __future__ import annotations

from tripbot import i18n
from tripbot.domain.flights import Offer
from tripbot.domain.money import Money, Rates
from tripbot.domain.ranking import Combo
from tripbot.domain.routes import PlannedLeg
from tripbot.i18n import gettext_noop
from tripbot.render.names import Names
from tripbot.sources.google import search_url
from tripbot.telegram.commands import NO_DATA, Reply, Request
from tripbot.telegram.context import BotContext
from tripbot.telegram.format import bold, esc

NO_LINK = gettext_noop("TBD link not available yet")


def links(context: BotContext, request: Request) -> Reply:
    window_id = context.window_arg(request.args)
    best = context.rankings().main[window_id].best if window_id else None
    if best is None:
        return Reply(text=esc(i18n._(NO_DATA)))
    rates = context.rankings().rates
    names = Names(context.config, context.reference)
    window = best.window
    body = [bold(f"{window.id} · {i18n.date_range(window.start, window.end)}")]
    for choice in best.choices:
        body += ["", bold(f"{choice.group.name} · {choice.group.headcount}×")]
        body += [
            _flight(i18n.pgettext("flight direction", "out"), choice.outbound, rates),
            _flight(i18n.pgettext("flight direction", "back"), choice.inbound, rates),
        ]
    people = sum(choice.group.headcount for choice in best.choices)
    body += ["", bold(f"{i18n._('Inside China')} · {people}×")]
    body += [_leg(leg, rates, names) for leg in best.route.legs]
    return Reply(text="\n".join([*body, "", *_sum(best, people)]))


def _flight(direction: str, offer: Offer, rates: Rates) -> str:
    numbers = " + ".join(segment.flight_number for segment in offer.segments)
    path = "→".join((offer.origin, *offer.via, offer.destination))
    url = search_url(offer, i18n.html_lang(), offer.price.currency)
    parts = [
        f"{direction} {i18n.numeric_day(offer.depart.date())}",
        numbers,
        " + ".join(offer.airlines),
        path,
        _price(offer.price, rates),
    ]
    return " · ".join(esc(part) for part in parts) + f' · <a href="{esc(url)}">Google Flights</a>'


def _leg(leg: PlannedLeg, rates: Rates, names: Names) -> str:
    option = leg.option
    price = _price(option.fare, rates)
    if option.estimate:
        price += f" {i18n.pgettext('fare estimate', 'est.')}"
    parts = [
        i18n.numeric_day(leg.day),
        option.name,
        f"{names.city(leg.origin)}→{names.city(leg.destination)}",
        price,
        i18n._(NO_LINK),
    ]
    return " · ".join(esc(part) for part in parts)


def _price(price: Money, rates: Rates) -> str:
    if price.currency == "EUR":
        return i18n.exact_money(price)
    return f"{i18n.exact_money(price)} ≈ {i18n.exact_money(rates.to_eur(price))}"


def _sum(best: Combo, people: int) -> list[str]:
    """Each group's tickets, then everyone's legs inside China, adding up to the total.

    Amounts keep their cents, so every row's arithmetic holds as written.
    """
    rows = [bold(i18n._("How the total adds up"))]
    running = Money.zero("EUR")
    for choice in best.choices:
        running += choice.flights_total_eur
        rows.append(
            _sum_row(
                choice.group.name, choice.group.headcount, choice.fare_eur, choice.flights_total_eur
            )
            + f" → {esc(i18n.exact_money(running))}"
        )
    running += best.internal_eur
    rows.append(
        _sum_row(i18n._("Inside China"), people, best.route.fare_eur, best.internal_eur)
        + f" → {esc(i18n.exact_money(running))}"
    )
    total = i18n.exact_money(best.total_eur)
    rows.append(bold(i18n._("Group total: {total}").format(total=total)))
    return rows


def _sum_row(label: str, count: int, each: Money, subtotal: Money) -> str:
    return esc(f"{label}: {count} × {i18n.exact_money(each)} = {i18n.exact_money(subtotal)}")
