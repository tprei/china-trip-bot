from datetime import date, datetime
from pathlib import Path

from telegram_fakes import sample_world

from tripbot.config import TripConfig
from tripbot.dashboard.build import build_dashboard, publish
from tripbot.reference import Reference
from tripbot.render.cards import CardRenderer
from tripbot.render.deck import Deck

NOW = datetime(2026, 11, 8, 3, 10)


def deck(config: TripConfig, reference: Reference) -> Deck:
    conn, report = sample_world(config, reference)
    return Deck(config, reference, conn, report.rankings, date(2026, 11, 8), CardRenderer())


def test_every_window_gets_a_panel_with_every_group(
    config: TripConfig, reference: Reference
) -> None:
    view = build_dashboard(deck(config, reference), NOW)
    assert [panel.id for panel in view.panels] == [w.id for w in config.windows]
    for panel in view.panels:
        assert panel.total is not None
        assert [g.name for g in panel.groups] == [g.name for g in config.groups]
        assert all(g.chosen is not None and g.outbound and g.inbound for g in panel.groups)
        assert sum(combo.best for combo in panel.combos) == 1
        assert panel.alternatives.rows[0].label == "All four cities"
    assert view.selected in {panel.id for panel in view.panels}
    assert view.updated == "Sun 8 Nov · 03:10"


def test_flights_are_cheapest_first_in_the_groups_currency(
    config: TripConfig, reference: Reference
) -> None:
    view = build_dashboard(deck(config, reference), NOW)
    lon = next(g for g in view.panels[0].groups if g.name == "Luísa + Gabi")
    prices = [int(item.price.strip("£").replace(",", "")) for item in lon.outbound]
    assert prices == sorted(prices)
    assert len(prices) <= 3


def test_site_is_one_self_contained_page(
    config: TripConfig, reference: Reference, tmp_path: Path
) -> None:
    path = publish(deck(config, reference), NOW, tmp_path / "site")
    html = path.read_text(encoding="utf-8")
    assert path.name == "index.html"
    assert 'id="tab-B"' in html and "checked" in html
    assert "Visa needed" in html and "Other plans" in html
    assert "<script" not in html
