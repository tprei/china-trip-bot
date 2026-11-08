"""Parse `/command args` and route it to a handler that returns a reply."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tripbot.telegram.api import Button
from tripbot.telegram.context import BotContext
from tripbot.telegram.format import bold, esc

NO_DATA = (
    "No complete combo yet: every group needs flights into the same city. Try after tonight's run."
)


@dataclass(frozen=True)
class Reply:
    text: str | None = None
    photo: bytes | None = None
    caption: str | None = None
    button: Button | None = None


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
    """`/best@trip_bot b` → ("best", ["b"]). Anything else → None."""
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    name, _, mention = words[0][1:].partition("@")
    if mention and bot_username and mention.lower() != bot_username.lower():
        return None
    return name.lower(), words[1:]


REGISTRY: dict[str, CommandSpec] = {}


def command(name: str, usage: str, help_text: str) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        REGISTRY[name] = CommandSpec(name, usage, help_text, handler)
        return handler

    return register


def dispatch(context: BotContext, request: Request) -> Reply | None:
    spec = REGISTRY.get(request.name)
    if spec is None:
        return None
    return spec.handler(context, request)


def details_button(context: BotContext) -> Button | None:
    return Button("Details", context.dashboard_url) if context.dashboard_url else None


def card(context: BotContext, render: Callable[..., bytes | None], *args: str) -> Reply:
    png = render(*args)
    if png is None:
        return Reply(text=NO_DATA)
    return Reply(photo=png, button=details_button(context))


@command("help", "/help", "the command list")
def help_command(context: BotContext, request: Request) -> Reply:
    lines = [bold("China 2027 bot")]
    lines += [f"{esc(spec.usage)} — {esc(spec.help)}" for spec in REGISTRY.values()]
    return Reply(text="\n".join(lines))


@command("summary", "/summary", "this week's card")
def summary_command(context: BotContext, request: Request) -> Reply:
    with context.open_deck(context) as deck:
        return card(context, deck.summary)


@command("banner", "/banner", "the route poster to share")
def banner_command(context: BotContext, request: Request) -> Reply:
    if context.banner_path is None or not context.banner_path.is_file():
        return Reply(text="No banner yet.")
    return Reply(photo=context.banner_path.read_bytes())


@command("best", "/best [window]", "best and fairest plan for a window")
def best_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=NO_DATA)
    with context.open_deck(context) as deck:
        return card(context, deck.summary, window)


@command("windows", "/windows", "the three windows side by side")
def windows_command(context: BotContext, request: Request) -> Reply:
    with context.open_deck(context) as deck:
        return Reply(photo=deck.windows(), button=details_button(context))


@command("other", "/other [window]", "the plan without Zhangjiajie, Shanghai or Yangshuo")
def other_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=NO_DATA)
    alternative = next(
        (alt for alt in context.config.route.alternatives if alt.id in request.args), None
    )
    with context.open_deck(context) as deck:
        if alternative is not None:
            return card(context, deck.route, window, alternative.id)
        return card(context, deck.other, window)


@command("route", "/route [window]", "day by day: stays, trains, sleepers, flights")
def route_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=NO_DATA)
    with context.open_deck(context) as deck:
        return card(context, deck.route, window)


@command("history", "/history [window]", "price chart")
def history_command(context: BotContext, request: Request) -> Reply:
    window = context.window_arg(request.args)
    if window is None:
        return Reply(text=NO_DATA)
    with context.open_deck(context) as deck:
        png = deck.history_card(window)
    if png is None:
        return Reply(text="History starts after two nights of data.")
    return Reply(photo=png, button=details_button(context))


@command("dashboard", "/dashboard", "open the full dashboard")
def dashboard_command(context: BotContext, request: Request) -> Reply:
    if not context.dashboard_url:
        return Reply(text="Dashboard not published yet.")
    return Reply(text=bold("Dashboard"), button=details_button(context))
