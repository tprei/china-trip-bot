"""Command-line entry point: `tripbot <command>`."""

from __future__ import annotations

import argparse
from pathlib import Path

from tripbot.config import load_config
from tripbot.domain.trip import people_count

DEFAULT_CONFIG = Path("config/trip.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripbot")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="validate config/trip.yaml and print a summary")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.command == "check-config":
        groups = list(config.groups)
        print(f"{people_count(groups)} people in {len(groups)} groups")
        for window in config.windows:
            print(f"window {window.id}: {window.label()} ({window.nights} nights)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
