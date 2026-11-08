import os
from datetime import date, datetime
from itertools import pairwise

import pytest
from factories import eur, offer
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from telegram_fakes import World

from tripbot import i18n
from tripbot.config import TripConfig
from tripbot.domain.money import Money
from tripbot.pipeline.nightly import NightlyReport
from tripbot.reference import Reference
from tripbot.render import details, views
from tripbot.render.cards import CardRenderer, render_html
from tripbot.render.deck import Deck
from tripbot.render.names import Names

NIGHT = datetime(2026, 11, 7, 3, 0)


def summary_inputs(
    config: TripConfig, reference: Reference, report: NightlyReport, window_id: str
) -> views.SummaryInputs:
    return views.SummaryInputs(
        names=Names(config, reference),
        rankings=report.rankings,
        window_id=window_id,
        reference_totals={},
        history={},
        health=report.health,
        today=date(2026, 11, 8),
        currencies=["BRL", "GBP"],
    )


def test_summary_view_shows_every_group_and_currency(
    config: TripConfig, reference: Reference, world: World
) -> None:
    _, report = world
    view = views.build_summary(summary_inputs(config, reference, report, "B"))
    assert view is not None
    assert [group.name for group in view.groups] == ["Thiago", "Luísa + Gabi", "David + Mari"]
    assert [money.flag for money in view.per_person] == ["eu", "br", "gb"]
    best = report.rankings.main["B"].best
    assert best is not None
    share = Money(best.total_eur.minor // 5, "EUR")
    brl = i18n.money(report.rankings.rates.from_eur(share, "BRL"))
    assert (view.per_person[0].text, view.per_person[1].text) == (i18n.money(share), brl)
    assert view.groups[1].price.startswith("£") and view.groups[1].price_note == "each"
    assert view.groups[2].outbound.note == "via MAD"
    assert [dot.id for dot in view.windows] == ["C", "A", "B"]
    assert sum(1 for dot in view.windows if dot.best) == 1
    stops = [item.name for item in view.timeline if item.kind == "stop"]
    assert {"Pequim", "Xangai", "Zhangjiajie", "Yangshuo"} <= set(stops)
    assert view.window_days == "14 days"
    assert sum(span.nights for span in view.days) == 13
    assert all(before.leave == after.arrive for before, after in pairwise(view.days))
    assert (view.days[0].arrive, view.days[-1].leave) == ("30 Aug", "12 Sep")


def test_flight_line_lists_stops_in_order_with_waits(
    config: TripConfig, reference: Reference
) -> None:
    found = offer(
        "sao",
        "outbound",
        ["GRU", "DOH", "PEK"],
        datetime(2027, 8, 28, 21, 0),
        eur(700),
        hours_per_hop=10,
        layover_hours=3,
        stops_on_board=("MAD",),
    )
    line = views.flight_line(found, Names(config, reference))
    assert [(stop.airport, stop.wait) for stop in line.stops] == [("MAD", ""), ("DOH", "3h")]
    assert (line.depart_time, line.arrive_time, line.arrive_shift) == ("21:00", "20:00", "+1")


def test_route_view_lists_stays_and_legs_in_order(
    config: TripConfig, reference: Reference, world: World
) -> None:
    _, report = world
    best = report.rankings.main["B"].best
    assert best is not None
    view = details.build_route(best, Names(config, reference), "Main plan")
    kinds = [step.kind for step in view.steps]
    assert kinds.count("leg") == len(best.route.legs)
    assert kinds.count("stay") >= len(best.route.stays)
    assert all(a != b for a, b in pairwise(kinds))


def test_compare_alternatives_marks_the_main_plan(
    config: TripConfig, reference: Reference, world: World
) -> None:
    _, report = world
    alternatives = {
        alt.id: (alt.label, report.rankings.alternatives["B"][alt.id])
        for alt in config.route.alternatives
    }
    view = details.compare_alternatives(
        report.rankings.main["B"], alternatives, Names(config, reference)
    )
    assert [row.label for row in view.rows] == [
        "All four cities",
        "Sem Zhangjiajie",
        "Sem Xangai",
        "Sem Yangshuo",
    ]
    assert view.rows[0].highlight and view.rows[0].delta is None


def test_summary_html_carries_the_numbers(
    config: TripConfig, reference: Reference, world: World
) -> None:
    _, report = world
    view = views.build_summary(summary_inputs(config, reference, report, "B"))
    assert view is not None
    html = render_html("summary.html.j2", view=view, people=5, selected="B")
    assert view.total in html
    assert "Luísa + Gabi" in html
    assert "data:font/woff2;base64," in html


@pytest.mark.usefixtures("pt_br")
def test_summary_renders_in_pt_br(config: TripConfig, reference: Reference, world: World) -> None:
    _, report = world
    view = views.build_summary(summary_inputs(config, reference, report, "B"))
    assert view is not None
    html = render_html("summary.html.j2", view=view, people=5, selected="B")
    assert '<html lang="pt-BR">' in html and "Resumo semanal" in html
    assert "Melhor plano · 5 pessoas" in html
    assert any(span.nights > 1 for span in view.days)
    assert " noites" in html


def chromium_available() -> bool:
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except PlaywrightError:
        return False
    return True


def test_cards_render_to_png(config: TripConfig, reference: Reference, world: World) -> None:
    # CI sets TRIPBOT_REQUIRE_CHROMIUM so a missing browser fails instead of skipping.
    if not chromium_available():
        if os.environ.get("TRIPBOT_REQUIRE_CHROMIUM"):
            pytest.fail("Chromium is required but could not start")
        pytest.skip("Chromium is not installed here")
    conn, report = world
    with CardRenderer() as renderer:
        deck = Deck(config, reference, conn, report.rankings, date(2026, 11, 8), renderer)
        png = deck.summary("B")
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
