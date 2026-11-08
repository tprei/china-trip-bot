"""Parse `/command args` and route it to a handler that returns a reply."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from tripbot import i18n
from tripbot.domain.ranking import Combo
from tripbot.i18n import gettext_noop
from tripbot.render.banner import shown_photos
from tripbot.render.names import Names
from tripbot.telegram.api import Button
from tripbot.telegram.context import BotContext, CardDeck
from tripbot.telegram.format import bold, esc

NO_DATA = gettext_noop(
    "No complete combo yet: every group needs flights into the same city. Try after tonight's run."
)


@dataclass(frozen=True)
class Reply:
    text: str | None = None
    photo: bytes | None = None
    album: tuple[bytes, ...] = ()
    caption: str | None = None
    button: Button | None = None
    pin: bool = False  # post to the bot's own topic and pin it in place of the last summary


@dataclass(frozen=True)
class Request:
    name: str
    args: list[str]
    sender: str


Handler = Callable[[BotContext, Request], Reply]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    help: str
    handler: Handler


def parse(text: str, bot_username: str | None = None) -> tuple[str, list[str]] | None:
    """Parse registered translated commands; unregistered commands return `None`."""
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    name, _, mention = words[0][1:].partition("@")
    if mention and bot_username and mention.lower() != bot_username.lower():
        return None
    command_name = f"/{name.lower()}"
    for spec in REGISTRY.values():
        if i18n._(spec.name).lower() == command_name:
            return name.lower(), words[1:]
    return None


REGISTRY: dict[str, CommandSpec] = {}


def command(name: str, usage: str, help_text: str) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        REGISTRY[name.removeprefix("/")] = CommandSpec(name, usage, help_text, handler)
        return handler

    return register


def dispatch(context: BotContext, request: Request) -> Reply | None:
    for spec in REGISTRY.values():
        if i18n._(spec.name).removeprefix("/").lower() == request.name.lower():
            return spec.handler(context, request)
    return None


def details_button(context: BotContext) -> Button | None:
    return Button(i18n._("Details"), context.dashboard_url) if context.dashboard_url else None


def details_link(context: BotContext) -> str | None:
    """Albums can't carry buttons, so their caption links the dashboard instead."""
    if not context.dashboard_url:
        return None
    return f'<a href="{esc(context.dashboard_url)}">{esc(i18n._("Details"))}</a>'


def card(context: BotContext, render: Callable[..., bytes | None], *args: str) -> Reply:
    png = render(*args)
    if png is None:
        return Reply(text=i18n._(NO_DATA))
    return Reply(photo=png, button=details_button(context))


def summary_album(context: BotContext, deck: CardDeck, window: str | None = None) -> Reply:
    """The weekly post: the summary pages, then the route on a map, as one album."""
    chosen = window or deck.best_window()
    pages = deck.summary(chosen) if chosen else None
    if chosen is None or not pages:
        return Reply(text=i18n._(NO_DATA))
    route_map = deck.route_map(chosen)
    album = pages if route_map is None else (*pages, route_map)
    if len(album) == 1:
        return Reply(photo=album[0], button=details_button(context))
    return Reply(album=album, caption=details_link(context))


@command(gettext_noop("/help"), gettext_noop("/help"), gettext_noop("the command list"))
def help_command(context: BotContext, request: Request) -> Reply:
    lines = [bold(i18n._("China 2027 bot"))]
    lines += [f"{esc(i18n._(spec.usage))} — {esc(i18n._(spec.help))}" for spec in REGISTRY.values()]
    return Reply(text="\n".join(lines))


@command(
    gettext_noop("/summary"),
    gettext_noop("/summary"),
    gettext_noop("this week's card"),
)
def summary_command(context: BotContext, request: Request) -> Reply:
    with context.open_deck(context) as deck:
        return summary_album(context, deck)


@command(
    gettext_noop("/publish"),
    gettext_noop("/publish"),
    gettext_noop("post the summary to the alerts topic and pin it"),
)
def publish_command(context: BotContext, request: Request) -> Reply:
    """The weekly post on demand, for when the plan changed: same album, same pin."""
    with context.open_deck(context) as deck:
        reply = summary_album(context, deck)
    if not reply.album and reply.photo is None:
        return reply
    return replace(reply, pin=True)


@command(
    gettext_noop("/banner"),
    gettext_noop("/banner [window]"),
    gettext_noop("the route poster to share"),
)
def banner_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    with context.open_deck(context) as deck:
        png = deck.banner(window)
    best = context.rankings().main[window].best
    if png is None or best is None:
        return Reply(text=i18n._(NO_DATA))
    return Reply(photo=png, caption=photo_credits(best, Names(context.config, context.reference)))


def photo_credits(best: Combo, names: Names) -> str:
    """Each photo's source and license as links, as their licenses ask; the poster can't link."""
    rows = [esc(i18n._("Photos, cropped, from Wikimedia Commons:"))]
    for city, photo in shown_photos(best, names):
        source = f'<a href="{esc(photo.source)}">{esc(city)}</a>'
        license_link = f'<a href="{esc(photo.license_url)}">{esc(photo.license)}</a>'
        rows.append(f"{source} · {esc(photo.author)} · {license_link}")
    return "\n".join(rows)


@command(
    gettext_noop("/best"),
    gettext_noop("/best [window]"),
    gettext_noop("best and fairest plan for a window"),
)
def best_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    with context.open_deck(context) as deck:
        return summary_album(context, deck, window)


@command(
    gettext_noop("/windows"),
    gettext_noop("/windows"),
    gettext_noop("the three windows side by side"),
)
def windows_command(context: BotContext, request: Request) -> Reply:
    with context.open_deck(context) as deck:
        return Reply(photo=deck.windows(), button=details_button(context))


@command(
    gettext_noop("/other"),
    gettext_noop("/other [window]"),
    gettext_noop("the plan without one of its cities"),
)
def other_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    alternative = next(
        (alt for alt in context.config.route.alternatives if alt.id in request.args), None
    )
    with context.open_deck(context) as deck:
        if alternative is not None:
            return card(context, deck.route, window, alternative.id)
        return card(context, deck.other, window)


@command(
    gettext_noop("/route"),
    gettext_noop("/route [window]"),
    gettext_noop("day by day: stays, trains, sleepers, flights"),
)
def route_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    with context.open_deck(context) as deck:
        return card(context, deck.route, window)


@command(
    gettext_noop("/map"),
    gettext_noop("/map [window]"),
    gettext_noop("the route on a map of China"),
)
def map_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    with context.open_deck(context) as deck:
        return card(context, deck.route_map, window)


@command(
    gettext_noop("/history"),
    gettext_noop("/history [window]"),
    gettext_noop("price chart"),
)
def history_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=i18n._(NO_DATA))
    with context.open_deck(context) as deck:
        png = deck.history_card(window)
    if png is None:
        return Reply(text=i18n._("History starts after two nights of data."))
    return Reply(photo=png, button=details_button(context))


@command(
    gettext_noop("/dashboard"),
    gettext_noop("/dashboard"),
    gettext_noop("open the full dashboard"),
)
def dashboard_command(context: BotContext, request: Request) -> Reply:
    if not context.dashboard_url:
        return Reply(text=i18n._("Dashboard not published yet."))
    return Reply(text=bold(i18n._("Dashboard")), button=details_button(context))
