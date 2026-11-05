from pathlib import Path

import pytest

from tripbot.config import TripConfig, load_config
from tripbot.domain.routes import PlannerInputs
from tripbot.reference import Reference, load_reference, planner_inputs

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config() -> TripConfig:
    return load_config(ROOT / "config" / "trip.yaml")


@pytest.fixture
def reference() -> Reference:
    return load_reference(ROOT / "data")


@pytest.fixture
def inputs(config: TripConfig, reference: Reference) -> PlannerInputs:
    return planner_inputs(config, reference, config.fallback_rates)
