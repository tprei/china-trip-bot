import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from telegram_fakes import World, sample_world

from tripbot.config import TripConfig, load_config
from tripbot.domain.routes import PlannerInputs
from tripbot.reference import Reference, load_reference, planner_inputs
from tripbot.store.db import connect

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def config() -> TripConfig:
    return load_config(ROOT / "config" / "trip.yaml")


@pytest.fixture(scope="session")
def reference() -> Reference:
    return load_reference(ROOT / "data")


@pytest.fixture
def inputs(config: TripConfig, reference: Reference) -> PlannerInputs:
    return planner_inputs(config, reference, config.fallback_rates)


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def sample_master(config: TripConfig, reference: Reference) -> Iterator[World]:
    """Two sample nights, run once per session; tests get a copy through `world`."""
    conn, report = sample_world(config, reference)
    yield conn, report
    conn.close()


@pytest.fixture
def world(sample_master: World) -> Iterator[World]:
    master, report = sample_master
    conn = connect(":memory:")
    master.backup(conn)
    yield conn, replace(report)
    conn.close()
