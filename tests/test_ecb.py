from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tripbot.sources.ecb import DAILY_URL, fetch_rates, parse_rates

FEED = (Path(__file__).parent / "fixtures" / "ecb-daily.xml").read_text(encoding="utf-8")


def test_parses_only_the_currencies_we_need() -> None:
    rates = parse_rates(FEED, {"GBP", "BRL", "CNY"})
    assert rates.as_of == date(2026, 11, 5)
    assert rates.per_eur == {
        "GBP": Decimal("0.8702"),
        "BRL": Decimal("6.2950"),
        "CNY": Decimal("7.7850"),
    }


def test_missing_currency_is_an_error() -> None:
    with pytest.raises(ValueError, match="ARS"):
        parse_rates(FEED, {"GBP", "ARS"})


def test_fetch_uses_the_daily_feed() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DAILY_URL
        return httpx.Response(200, text=FEED)

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        rates = fetch_rates(client, {"EUR", "MXN"})
    assert rates.per_eur == {"MXN": Decimal("21.0410")}
