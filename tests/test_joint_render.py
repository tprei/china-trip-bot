from dataclasses import replace

from test_roundtrip import quote

from tripbot.domain.money import Money
from tripbot.domain.trip import Group
from tripbot.render.cards import render_html
from tripbot.render.joint import joint_card


def test_joint_card_preserves_observed_group_total() -> None:
    observed = replace(
        quote(Group("g", "Two people", "dub", ("A", "B"))),
        total=Money(200100, "EUR"),
        per_person=Money(100100, "EUR"),
        fare_notes="Itinerary will be split across multiple bookings and flight tickets",
    )
    view = joint_card("Two people", observed)
    assert view.total == "€2,001"
    assert view.per_person == "€1,001"
    assert any("Multiple bookings" in line for line in view.warnings)
    assert any("Rounded" in line for line in view.warnings)
    html = render_html("joint.html.j2", view=view)
    assert "€2,001" in html and "€2,002" not in html
    assert "not checkout" in html
    assert "no separate one-way fares" in html
    assert "https://" not in html


def test_joint_summary_labels_estimates_separately() -> None:
    html = render_html(
        "joint.html.j2",
        view=None,
        groups=(),
        total="€4,000",
        internal="€500.00",
        observed="01 Jan 2030 · 12:00 UTC",
    )
    assert "Internal transport estimate: €500.00" in html
    assert "One sampled combo" in html
    assert "guaranteed savings" in html
    assert "hotel / route review" not in html
    assert "50 min at HKG" not in html


def test_summary_and_dashboard_use_supplied_groups() -> None:
    view = joint_card("Fictional group", quote(Group("g", "Group", "dub", ("A", "B"))))
    html = render_html(
        "joint.html.j2",
        view=None,
        groups=(view,),
        total=view.total,
        internal="€100",
        observed="Synthetic",
    )
    assert "2 people · 1 groups" in html
    assert "Fictional group: Rounded" in html
    dashboard = render_html(
        "dashboard.html.j2",
        view={"updated": "Synthetic"},
        joint={"total": view.total, "internal": "€100", "groups": (view,)},
    )
    assert "2 people · 1 groups" in dashboard
    assert "30 Aug–10 Sep" not in dashboard
