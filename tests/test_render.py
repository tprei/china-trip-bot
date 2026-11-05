import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from tripbot.config import Budgets, TripConfig
from tripbot.pipeline.nightly import NightlyDeps, NightlyReport, run_nightly
from tripbot.reference import Reference
from tripbot.render import views
from tripbot.render.names import Names
from tripbot.render.samples import SampleSource
from tripbot.store.db import connect

NIGHT = datetime(2026, 11, 7, 3, 0)


@pytest.fixture
def world(config: TripConfig, reference: Reference) -> tuple[sqlite3.Connection, NightlyReport]:
    conn = connect(":memory:")
    full = replace(config, budgets=Budgets(google=0, serpapi=1000, tripcom=0))
    report = None
    for night, discount in enumerate((0, 6)):
        deps = NightlyDeps(full, reference, conn, [SampleSource(discount)], log=lambda _: None)
        report = run_nightly(deps, NIGHT + timedelta(days=night))
    assert report is not None
    return conn, report


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
    config: TripConfig, reference: Reference, world: tuple[sqlite3.Connection, NightlyReport]
) -> None:
    _, report = world
    view = views.build_summary(summary_inputs(config, reference, report, "B"))
    assert view is not None
    assert [group.name for group in view.groups] == ["Thiago", "Luísa + Gabi", "David + Mari"]
    assert [money.flag for money in view.others] == ["br", "gb"]
    assert view.groups[1].price.startswith("£") and view.groups[1].price_note == "each"
    assert view.groups[2].outbound.note == "via MAD"
    assert [dot.id for dot in view.windows] == ["C", "A", "B"]
    assert sum(1 for dot in view.windows if dot.best) == 1
    stops = [item.name for item in view.timeline if item.kind == "stop"]
    assert {"Beijing", "Shanghai", "Zhangjiajie", "Yangshuo"} <= set(stops)
