from configparser import RawConfigParser
from datetime import date
from pathlib import Path

import pytest
from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
from babel.messages.pofile import read_po

from tripbot import i18n
from tripbot.domain.money import Money

ROOT = Path(__file__).resolve().parent.parent


def source_messages() -> set[tuple[str | None, str]]:
    """What `pybabel extract` finds with `babel.cfg`: (context, msgid) pairs."""
    mapping = RawConfigParser()
    mapping.read(ROOT / "babel.cfg", encoding="utf-8")
    method_map: list[tuple[str, str]] = []
    options_map: dict[str, dict[str, str]] = {}
    for section in mapping.sections():
        method, pattern = (part.strip() for part in section.split(":", 1))
        method_map.append((pattern, method))
        options_map[pattern] = dict(mapping.items(section))
    keywords = {**DEFAULT_KEYWORDS, "gettext_noop": None}
    found: set[tuple[str | None, str]] = set()
    for _, _, message, _, context in extract_from_dir(
        ROOT / "src", method_map, options_map, keywords=keywords
    ):
        found.add((context, message if isinstance(message, str) else message[0]))
    return found


def test_every_source_string_has_a_pt_br_translation() -> None:
    with i18n.catalog_path("pt_BR").open("rb") as po:
        catalog = read_po(po)
    translated = {
        (message.context, message.id if isinstance(message.id, str) else message.id[0])
        for message in catalog
        if message.id
        and not message.fuzzy
        and all(message.string if isinstance(message.string, tuple) else (message.string,))
    }
    missing = sorted(str(key) for key in source_messages() - translated)
    assert missing == [], "run scripts/update-translations.sh and translate: " + ", ".join(missing)


@pytest.mark.usefixtures("pt_br")
def test_pt_br_money_and_dates() -> None:
    assert i18n.money(Money(947_949, "EUR")) == "€\xa09.479"
    assert i18n.money(Money(1_173_700, "BRL")) == "R$\xa011.737"
    assert (
        i18n.date_range(date(2027, 8, 30), date(2027, 9, 12)) == "30 de ago.\u2009–\u200912 de set."
    )
