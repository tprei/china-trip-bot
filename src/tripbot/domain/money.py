"""Money as integer minor units, and conversion through EUR reference rates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

SYMBOLS = {"EUR": "€", "GBP": "£", "BRL": "R$ ", "USD": "US$ ", "MXN": "MX$ ", "CNY": "¥"}


@dataclass(frozen=True, order=True)
class Money:
    """An amount in the currency's minor unit (cents, pence, centavos)."""

    minor: int
    currency: str

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str) -> Money:
        """Money from a major-unit amount, e.g. `Money.of("12.50", "EUR")`."""
        minor = (Decimal(amount) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return cls(int(minor), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    def __add__(self, other: Money) -> Money:
        _require_same_currency(self, other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        _require_same_currency(self, other)
        return Money(self.minor - other.minor, self.currency)

    def times(self, count: int) -> Money:
        return Money(self.minor * count, self.currency)

    def whole(self) -> str:
        """Rounded to whole units with a thousands separator: `€5,651`."""
        units = (Decimal(self.minor) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        sign = "−" if units < 0 else ""
        return f"{sign}{SYMBOLS.get(self.currency, self.currency + ' ')}{abs(units):,}"


def _require_same_currency(a: Money, b: Money) -> None:
    if a.currency != b.currency:
        raise ValueError(f"cannot combine {a.currency} and {b.currency}")


def total(amounts: list[Money], currency: str) -> Money:
    result = Money.zero(currency)
    for amount in amounts:
        result = result + amount
    return result


@dataclass(frozen=True)
class Rates:
    """Reference rates: one EUR buys `per_eur[code]` of that currency."""

    per_eur: Mapping[str, Decimal]
    as_of: date

    def rate(self, currency: str) -> Decimal:
        if currency == "EUR":
            return Decimal(1)
        if currency not in self.per_eur:
            raise KeyError(f"no rate for {currency}")
        return self.per_eur[currency]

    def to_eur(self, amount: Money) -> Money:
        minor = (Decimal(amount.minor) / self.rate(amount.currency)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return Money(int(minor), "EUR")

    def from_eur(self, amount: Money, currency: str) -> Money:
        _require_same_currency(amount, Money.zero("EUR"))
        minor = (Decimal(amount.minor) * self.rate(currency)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return Money(int(minor), currency)
