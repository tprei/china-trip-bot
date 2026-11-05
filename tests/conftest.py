from pathlib import Path

import pytest

from tripbot.config import TripConfig, load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config() -> TripConfig:
    return load_config(ROOT / "config" / "trip.yaml")
