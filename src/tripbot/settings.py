"""Runtime settings from the environment and an optional `.env` file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    db_path: Path
    config_path: Path
    data_dir: Path
    site_dir: Path
    banner_path: Path
    serpapi_key: str | None
    telegram_token: str | None
    telegram_chat_id: str | None
    telegram_topic_id: int | None
    healthcheck_url: str | None
    dashboard_url: str | None


def read_dotenv(path: Path) -> dict[str, str]:
    """`KEY=value` lines; `#` comments and blank lines are skipped."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(root: Path, env: Mapping[str, str] | None = None) -> Settings:
    merged = {**read_dotenv(root / ".env"), **(env if env is not None else os.environ)}

    def path(name: str, default: str) -> Path:
        value = Path(merged.get(name, default))
        return value if value.is_absolute() else root / value

    def optional(name: str) -> str | None:
        value = merged.get(name, "").strip()
        return value or None

    return Settings(
        root=root,
        db_path=path("TRIPBOT_DB", "var/tripbot.sqlite3"),
        config_path=path("TRIPBOT_CONFIG", "config/trip.yaml"),
        data_dir=path("TRIPBOT_DATA", "data"),
        site_dir=path("TRIPBOT_SITE", "site"),
        banner_path=path("TRIPBOT_BANNER", "var/banner.png"),
        serpapi_key=optional("SERPAPI_KEY"),
        telegram_token=optional("TELEGRAM_TOKEN"),
        telegram_chat_id=optional("TELEGRAM_CHAT_ID"),
        telegram_topic_id=int(topic) if (topic := optional("TELEGRAM_TOPIC_ID")) else None,
        healthcheck_url=optional("HEALTHCHECK_URL"),
        dashboard_url=optional("DASHBOARD_URL"),
    )
