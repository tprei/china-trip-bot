"""Command-line entry point: `tripbot <command>`."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import httpx

from tripbot.config import load_config
from tripbot.domain.trip import people_count
from tripbot.pipeline.health import ping
from tripbot.pipeline.nightly import NightlyDeps, NightlyReport, run_nightly
from tripbot.reference import load_reference
from tripbot.render.samples import render_samples
from tripbot.settings import Settings, load_settings
from tripbot.sources.base import FlightSource
from tripbot.sources.ecb import fetch_rates
from tripbot.sources.google import GoogleFlights
from tripbot.sources.serpapi import SerpApi
from tripbot.sources.tripcom import TripCom
from tripbot.store.db import connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripbot")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="validate config/trip.yaml and print a summary")
    nightly = commands.add_parser("nightly", help="search, rank, snapshot and alert")
    nightly.add_argument("--no-search", action="store_true", help="rank stored offers only")
    samples = commands.add_parser("render-samples", help="render every card from sample flights")
    samples.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    settings = load_settings(args.root.resolve())
    if args.command == "check-config":
        return check_config(settings)
    if args.command == "nightly":
        return nightly_command(settings, search=not args.no_search)
    if args.command == "render-samples":
        config = load_config(settings.config_path)
        for path in render_samples(config, load_reference(settings.data_dir), args.out):
            print(path)
        return 0
    return 2


def check_config(settings: Settings) -> int:
    config = load_config(settings.config_path)
    load_reference(settings.data_dir)
    groups = list(config.groups)
    print(f"{people_count(groups)} people in {len(groups)} groups")
    for window in config.windows:
        print(f"window {window.id}: {window.label()} ({window.nights} nights)")
    return 0


def nightly_command(settings: Settings, *, search: bool) -> int:
    config = load_config(settings.config_path)
    reference = load_reference(settings.data_dir)
    conn = connect(settings.db_path)
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
        report = run_nightly(deps, datetime.now())
        print_report(report)
        ping(client, settings.healthcheck_url, report.ok)
    return 0 if report.ok else 1


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
