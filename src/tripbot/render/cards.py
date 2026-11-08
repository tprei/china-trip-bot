"""Render card templates to PNG with headless Chromium."""

from __future__ import annotations

import base64
from pathlib import Path
from types import TracebackType
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from playwright.sync_api import Browser, Playwright, sync_playwright

from tripbot import i18n

HERE = Path(__file__).parent
FONTS = HERE / "fonts"
CARD_WIDTH = 1080
LOAD_TIMEOUT_MS = 15_000


def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=["jinja2.ext.i18n"],
    )
    env.globals.update(gettext=i18n._, ngettext=i18n.ngettext, pgettext=i18n.pgettext)
    return env


def font_urls() -> dict[str, str]:
    def data_url(name: str) -> str:
        encoded = base64.b64encode((FONTS / name).read_bytes()).decode("ascii")
        return f"data:font/woff2;base64,{encoded}"

    return {
        "archivo": data_url("archivo.woff2"),
        "mono_400": data_url("jetbrains-mono-400.woff2"),
        "mono_700": data_url("jetbrains-mono-700.woff2"),
    }


def render_html(template: str, **context: Any) -> str:
    return (
        environment()
        .get_template(template)
        .render(fonts=font_urls(), lang=i18n.html_lang(), **context)
    )


class CardRenderer:
    """One Chromium for a batch of cards: `with CardRenderer() as cards: cards.png(html)`."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> CardRenderer:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def png(self, html: str) -> bytes:
        if self._browser is None:
            raise RuntimeError("use CardRenderer as a context manager")
        page = self._browser.new_page(viewport={"width": CARD_WIDTH, "height": 800})
        try:
            page.set_content(html, wait_until="load", timeout=LOAD_TIMEOUT_MS)
            page.evaluate("document.fonts.ready")
            return page.locator(".card").screenshot(type="png")
        finally:
            page.close()

    def pngs(self, html: str) -> tuple[bytes, ...]:
        """Every `.card` in the document, in order: one page load for a set of pages."""
        if self._browser is None:
            raise RuntimeError("use CardRenderer as a context manager")
        page = self._browser.new_page(viewport={"width": CARD_WIDTH, "height": 800})
        try:
            page.set_content(html, wait_until="load", timeout=LOAD_TIMEOUT_MS)
            page.evaluate("document.fonts.ready")
            cards = page.locator(".card")
            return tuple(cards.nth(i).screenshot(type="png") for i in range(cards.count()))
        finally:
            page.close()
