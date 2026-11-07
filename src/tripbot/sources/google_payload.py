"""Narrow HTML compatibility layer for pinned faster-flights 3.8.0.

Keep its transport and flight model parser; do not split JSON on commas.
"""

import json
import re

from fast_flights.fetcher import fetch_flights_html
from fast_flights.parser import MetaList, parse_payload
from fast_flights.querying import Query
from selectolax.lexbor import LexborHTMLParser

_PREFIX = re.compile(r"AF_initDataCallback\(\{key: *'ds:1', *hash: *'[^']*', *data: *")


def parse_html(html: str) -> MetaList:
    """Require exactly one flight callback, and consume its entire envelope."""
    candidates = [
        node.text().strip()
        for node in LexborHTMLParser(html).css("script")
        if _PREFIX.match(node.text().strip())
    ]
    if len(candidates) != 1:
        raise ValueError("google missing or ambiguous flight callback")
    return parse_callback(candidates[0])


def parse_callback(script: str) -> MetaList:
    prefix = _PREFIX.match(script)
    if prefix is None:
        raise ValueError("google unrecognized flight callback")
    decoder = json.JSONDecoder()
    payload, end = decoder.raw_decode(script, prefix.end())
    remainder = script[end:].strip()
    # sideChannel is a separate JSON object, not part of the flight array.
    # Decode it too: never silently discard arbitrary JS or additional JSON.
    marker = ", sideChannel:"
    if not remainder.startswith(marker):
        raise ValueError("google missing sideChannel envelope")
    side_text = remainder[len(marker) :].lstrip()
    side_channel, end = decoder.raw_decode(side_text)
    if not isinstance(side_channel, dict) or side_text[end:].strip() != "});":
        raise ValueError("google unexpected callback remainder")
    if not isinstance(payload, list) or len(payload) < 4:
        raise ValueError("google invalid flight payload")
    # 3.8.0 defaults to main results only, losing top departing flights.
    return parse_payload(payload, include_top_results=True)


def get_flights(query: Query) -> MetaList:
    return parse_html(fetch_flights_html(query))
