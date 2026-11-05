"""Telegram HTML helpers. Every dynamic string goes through `esc`."""

from __future__ import annotations

import html


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def bold(text: str) -> str:
    return f"<b>{esc(text)}</b>"


def code(text: str) -> str:
    return f"<code>{esc(text)}</code>"


def lines(*parts: str) -> str:
    return "\n".join(part for part in parts if part)
