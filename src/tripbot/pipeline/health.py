"""Dead-man's switch: tell healthchecks.io the run finished, or failed."""

from __future__ import annotations

from contextlib import suppress

import httpx


def ping(client: httpx.Client, url: str | None, ok: bool) -> None:
    """A missed or failed ping makes healthchecks.io email us."""
    if not url:
        return
    target = url if ok else url.rstrip("/") + "/fail"
    with suppress(httpx.HTTPError):  # a missing ping is itself the signal
        client.get(target, timeout=15)
