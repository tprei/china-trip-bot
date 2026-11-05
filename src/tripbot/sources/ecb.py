"""European Central Bank daily reference rates (one EUR in other currencies)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import httpx

from tripbot.domain.money import Rates

DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def fetch_rates(client: httpx.Client, currencies: set[str]) -> Rates:
    response = client.get(DAILY_URL, timeout=30)
    response.raise_for_status()
    return parse_rates(response.text, currencies)


def parse_rates(xml: str, currencies: set[str]) -> Rates:
    root = ET.fromstring(xml)
    day = next(
        (cube for cube in root.iter() if cube.tag.endswith("Cube") and "time" in cube.attrib),
        None,
    )
    if day is None:
        raise ValueError("no dated Cube element in ECB feed")
    per_eur = {
        cube.attrib["currency"]: Decimal(cube.attrib["rate"])
        for cube in day
        if cube.attrib.get("currency") in currencies
    }
    missing = currencies - set(per_eur) - {"EUR"}
    if missing:
        raise ValueError(f"ECB feed has no rate for {', '.join(sorted(missing))}")
    return Rates(per_eur=per_eur, as_of=date.fromisoformat(day.attrib["time"]))
