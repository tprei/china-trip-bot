"""The language the group reads: gettext catalogs plus CLDR formats for money and dates.

Code and templates hold English source strings wrapped in `_()`, `ngettext()` or
`pgettext()`. Each translation lives in `locales/<locale>/LC_MESSAGES/tripbot.po`, and
`scripts/update-translations.sh` adds new source strings to it. `activate()` picks the
language once per process, from `trip.yaml`.
"""

from __future__ import annotations

import gettext
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path

from babel import Locale
from babel.dates import format_date
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.numbers import format_currency

from tripbot.domain.money import Money

DOMAIN = "tripbot"
LOCALES = Path(__file__).parent / "locales"
SOURCE_LOCALE = "en"


@dataclass(frozen=True)
class Language:
    locale: Locale
    translations: gettext.NullTranslations


def load(code: str) -> Language:
    """The catalog for `code` (e.g. `pt_BR`). English is the source and needs none."""
    locale = Locale.parse(code)
    if str(locale) == SOURCE_LOCALE:
        return Language(locale, gettext.NullTranslations())
    path = catalog_path(str(locale))
    if not path.is_file():
        raise ValueError(f"no translations for {locale} at {path}")
    with path.open("rb") as po:
        catalog = read_po(po, locale=locale, domain=DOMAIN)
    compiled = BytesIO()
    write_mo(compiled, catalog)
    compiled.seek(0)
    return Language(locale, gettext.GNUTranslations(compiled))


def catalog_path(code: str) -> Path:
    return LOCALES / code / "LC_MESSAGES" / f"{DOMAIN}.po"


@dataclass
class _Current:
    language: Language


_current = _Current(load(SOURCE_LOCALE))


def activate(code: str) -> None:
    _current.language = load(code)


def active() -> Language:
    return _current.language


def _(message: str) -> str:
    return _current.language.translations.gettext(message)


def ngettext(singular: str, plural: str, count: int) -> str:
    return _current.language.translations.ngettext(singular, plural, count)


def pgettext(context: str, message: str) -> str:
    return _current.language.translations.pgettext(context, message)


def gettext_noop(message: str) -> str:
    """Mark a string for extraction without translating it; pass it through `_()` when shown.

    For text fixed at import time, before `activate()` runs: command names and help.
    """
    return message


def money(amount: Money) -> str:
    """Whole units in the locale's own pattern: `€ 9.479` in pt_BR, `€9,479` in en."""
    locale = _current.language.locale
    pattern = locale.currency_formats["standard"].pattern.replace(".00", "")
    return format_currency(
        amount.units(), amount.currency, format=pattern, locale=locale, currency_digits=False
    )


def day(value: date) -> str:
    """`dom. 29 ago.` in pt_BR, `Sun 29 Aug` in en."""
    return format_date(value, "EEE d MMM", locale=_current.language.locale)


def short_day(value: date) -> str:
    """`29 ago.` in pt_BR, `29 Aug` in en."""
    return format_date(value, "d MMM", locale=_current.language.locale)


def date_range(start: date, end: date) -> str:
    """`30 ago. – 12 set.` in pt_BR, `30 Aug – 12 Sep` in en; a shared month once: `9 – 22 ago.`."""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} – {short_day(end)}"
    return f"{short_day(start)} – {short_day(end)}"


def html_lang() -> str:
    """`pt-BR` for the `<html lang>` attribute."""
    return str(_current.language.locale).replace("_", "-")
