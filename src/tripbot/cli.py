"""Command-line entry point: `tripbot <command>`."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from tripbot import i18n
from tripbot.config import TripConfig, load_config
from tripbot.dashboard.build import publish
from tripbot.domain.trip import people_count
from tripbot.pipeline.group_search import load_plan, run_groups
from tripbot.pipeline.health import ping
from tripbot.pipeline.nightly import NightlyDeps, NightlyReport, run_nightly
from tripbot.pipeline.state import Rankings, load_rankings
from tripbot.reference import Reference, load_reference
from tripbot.render.cards import CardRenderer
from tripbot.render.deck import Deck
from tripbot.render.samples import render_samples
from tripbot.settings import Settings, load_settings
from tripbot.sources.base import FlightSource
from tripbot.sources.ecb import fetch_rates
from tripbot.sources.google import GoogleFlights
from tripbot.sources.serpapi import SerpApi
from tripbot.sources.tripcom import TripCom
from tripbot.sources.tripcom_browser import TripComBrowserSource
from tripbot.sources.tripcom_fallback import TripComFallback
from tripbot.store.db import connect
from tripbot.telegram.api import TelegramApi
from tripbot.telegram.bot import BotDeps, run_forever
from tripbot.telegram.notify import notify_nightly


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripbot")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="validate config/trip.yaml and print a summary")
    nightly = commands.add_parser("nightly", help="search, rank, snapshot and alert")
    nightly.add_argument("--no-search", action="store_true", help="rank stored offers only")
    commands.add_parser("bot", help="answer Telegram commands and send the weekly summary")
    commands.add_parser("build-dashboard", help="write site/index.html from stored offers")
    samples = commands.add_parser("render-samples", help="render every card from sample flights")
    samples.add_argument("out", type=Path)
    group_search = commands.add_parser("search-groups", help="isolated joint-fare comparison")
    group_search.add_argument("plan", type=Path)
    group_search.add_argument("output", type=Path)
    group_search.add_argument("--window", default="B")
    group_search.add_argument("--browser-fallback", action="store_true")
    group_search.add_argument("--budget", type=int, default=3)
    args = parser.parse_args(argv)

    settings = load_settings(args.root.resolve())
    if args.command == "search-groups":
        return group_search_command(
            settings, args.plan, args.output, args.window, args.browser_fallback, args.budget
        )
    if args.command == "check-config":
        return check_config(settings)
    if args.command == "nightly":
        return nightly_command(settings, search=not args.no_search)
    if args.command == "bot":
        return bot_command(settings)
    if args.command == "build-dashboard":
        return dashboard_command(settings)
    if args.command == "render-samples":
        config = load_trip(settings)
        for path in render_samples(config, load_reference(settings.data_dir), args.out):
            print(path)
        return 0
    return 2


def group_search_command(
    settings: Settings,
    plan_path: Path,
    output: Path,
    window: str,
    browser_enabled: bool,
    budget: int,
) -> int:
    config = load_trip(settings)
    plan = load_plan(plan_path, config, window)
    if not 0 <= budget <= config.budgets.tripcom:
        raise ValueError("budget exceeds configured Trip.com limit")
    if len(plan.requests) * 2 > config.budgets.google:
        raise ValueError("plan exceeds configured Google budget")
    if len(plan.requests) > budget:
        raise ValueError("budget cannot cover plan")
    browser = TripComBrowserSource(output / "browser") if browser_enabled else None
    with httpx.Client(follow_redirects=True) as client:
        provider = TripComFallback(TripCom(client), browser, budget=budget)
        path = run_groups(
            config, load_reference(settings.data_dir), plan, provider, GoogleFlights(), output
        )
    print(path)
    return 0


def check_config(settings: Settings) -> int:
    config = load_config(settings.config_path)
    load_reference(settings.data_dir)
    groups = list(config.groups)
    print(f"{people_count(groups)} people in {len(groups)} groups")
    for window in config.windows:
        print(f"window {window.id}: {window.label()} ({window.nights} nights)")
    return 0


def load_trip(settings: Settings) -> TripConfig:
    """The trip config, with its language active for everything the group reads."""
    config = load_config(settings.config_path)
    i18n.activate(config.locale)
    return config


def nightly_command(settings: Settings, *, search: bool) -> int:
    config = load_trip(settings)
    reference = load_reference(settings.data_dir)
    with closing(connect(settings.db_path)) as conn:
        currencies = {origin.currency for origin in config.origins.values()} | {"CNY", "USD"}
        with httpx.Client(follow_redirects=True) as client:
            sources: list[FlightSource] = []
            if search:
                sources.append(GoogleFlights())
                if settings.serpapi_key:
                    sources.append(SerpApi(client, settings.serpapi_key))
                sources.append(TripCom(client))
            deps = NightlyDeps(
                config=config,
                reference=reference,
                conn=conn,
                sources=sources,
                fetch_rates=(lambda: fetch_rates(client, currencies)) if search else None,
            )
            now = local_now(config)
            report = run_nightly(deps, now)
            print_report(report)
            print(build_site(settings, config, reference, conn, report.rankings, now))
            if settings.telegram_token and settings.telegram_chat_id:
                bot = bot_deps(
                    settings, config, reference, conn, TelegramApi(client, settings.telegram_token)
                )
                notify_nightly(
                    bot.messenger, bot.chat_id, report, bot.context(now), topic=bot.topic
                )
            ping(client, settings.healthcheck_url, report.ok)
        return 0 if report.ok else 1


def dashboard_command(settings: Settings) -> int:
    config = load_trip(settings)
    reference = load_reference(settings.data_dir)
    with closing(connect(settings.db_path)) as conn:
        now = local_now(config)
        rankings = load_rankings(conn, config, reference, now)
        print(build_site(settings, config, reference, conn, rankings, now))
        return 0


def build_site(
    settings: Settings,
    config: TripConfig,
    reference: Reference,
    conn: sqlite3.Connection,
    rankings: Rankings,
    now: datetime,
) -> Path:
    # The deck only supplies numbers here; its renderer never starts Chromium.
    deck = Deck(config, reference, conn, rankings, now.date(), CardRenderer())
    return publish(deck, now, settings.site_dir)


def local_now(config: TripConfig) -> datetime:
    return datetime.now(ZoneInfo(config.timezone)).replace(tzinfo=None)


def bot_deps(
    settings: Settings,
    config: TripConfig,
    reference: Reference,
    conn: sqlite3.Connection,
    api: TelegramApi,
) -> BotDeps:
    if not settings.telegram_chat_id:
        raise SystemExit("TELEGRAM_CHAT_ID is not set")
    return BotDeps(
        config=config,
        reference=reference,
        conn=conn,
        messenger=api,
        chat_id=settings.telegram_chat_id,
        topic=settings.telegram_topic_id,
        dashboard_url=settings.dashboard_url,
        banner_path=settings.banner_path,
    )


def bot_command(settings: Settings) -> int:
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_TOKEN is not set")
    config = load_trip(settings)
    reference = load_reference(settings.data_dir)
    with closing(connect(settings.db_path)) as conn:
        with httpx.Client() as client:
            api = TelegramApi(client, settings.telegram_token)
            run_forever(bot_deps(settings, config, reference, conn, api), api)
        return 0


def print_report(report: NightlyReport) -> None:
    for source, (worked, tried) in sorted(report.health.items()):
        print(f"{source}: {worked}/{tried} searches worked")
    for name in report.failing_sources:
        print(f"{name}: failing for several runs")
    for note in report.notes:
        print(note)
    for window_id, ranking in report.rankings.main.items():
        best = ranking.best
        if best is None:
            print(f"window {window_id}: no complete combo yet")
            continue
        print(f"window {window_id}: {best.total_eur.whole()} via {best.entry} → {best.exit}")
    for alert in report.alerts:
        print(f"ALERT {alert.kind} window {alert.window_id}: {alert.total.whole()}")


if __name__ == "__main__":
    raise SystemExit(main())
