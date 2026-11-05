from datetime import date
from decimal import Decimal

import pytest

from tripbot.domain.money import Money, Rates, total

RATES = Rates(per_eur={"GBP": Decimal("0.87"), "BRL": Decimal("6.30")}, as_of=date(2026, 11, 5))


def test_of_rounds_half_up_to_minor_units() -> None:
    assert Money.of("12.345", "EUR") == Money(1235, "EUR")
    assert Money.of(10, "GBP") == Money(1000, "GBP")


def test_adding_different_currencies_fails() -> None:
    with pytest.raises(ValueError):
        Money(100, "EUR") + Money(100, "GBP")


def test_total_and_times() -> None:
    amounts = [Money(1000, "EUR"), Money(250, "EUR").times(2)]
    assert total(amounts, "EUR") == Money(1500, "EUR")


def test_conversion_rounds_once_half_up() -> None:
    assert RATES.to_eur(Money(52000, "GBP")) == Money(59770, "EUR")
    assert RATES.from_eur(Money(565100, "EUR"), "BRL") == Money(3560130, "BRL")
    assert RATES.to_eur(Money(100, "EUR")) == Money(100, "EUR")


def test_missing_rate_is_an_error() -> None:
    with pytest.raises(KeyError):
        RATES.to_eur(Money(100, "MXN"))


def test_whole_formats_with_symbol_and_separator() -> None:
    assert Money(565149, "EUR").whole() == "€5,651"
    assert Money(894000, "BRL").whole() == "R$ 8,940"
    assert Money(-21200, "EUR").whole() == "−€212"
