"""Render a fictional joint-fare card offline; never query or publish."""

import sys
from datetime import date, datetime
from pathlib import Path

from tripbot.domain.flights import Segment
from tripbot.domain.money import Money
from tripbot.domain.roundtrip import Journey, RoundTripQuote, RoundTripRequest
from tripbot.render.cards import CardRenderer, render_html
from tripbot.render.joint import joint_card


def main() -> None:
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=False)
    request = RoundTripRequest("example", "AAA", "BBB", date(2030, 1, 1), date(2030, 1, 10), 2)
    outbound = Journey(
        (
            Segment(
                "AAA",
                "BBB",
                datetime(2030, 1, 1, 8),
                datetime(2030, 1, 1, 10),
                120,
                "XX",
                "Example Air",
                "XX100",
            ),
        ),
        120,
    )
    inbound = Journey(
        (
            Segment(
                "BBB",
                "AAA",
                datetime(2030, 1, 10, 8),
                datetime(2030, 1, 10, 10),
                120,
                "XX",
                "Example Air",
                "XX101",
            ),
        ),
        120,
    )
    quote = RoundTripQuote(
        request,
        outbound,
        inbound,
        Money(200100, "EUR"),
        Money(100100, "EUR"),
        datetime(2030, 1, 1),
        "synthetic",
        None,
        "Fictional fixture",
    )
    html = render_html("joint.html.j2", view=joint_card("Fictional group", quote))
    (output / "joint.html").write_text(html, encoding="utf-8")
    with CardRenderer() as renderer:
        (output / "joint.png").write_bytes(renderer.png(html))


if __name__ == "__main__":
    main()
