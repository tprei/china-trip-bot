import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from tripbot.cli import group_search_command
from tripbot.config import TripConfig
from tripbot.domain.roundtrip import RoundTripRequest
from tripbot.domain.searches import SearchKey, SearchResult
from tripbot.pipeline.group_search import GroupRunPlan, load_plan, run_groups, validate_plan
from tripbot.reference import Reference
from tripbot.settings import Settings, load_settings
from tripbot.sources.base import SearchShape
from tripbot.sources.tripcom import TripCom
from tripbot.sources.tripcom_fallback import TripComFallback

ROOT = Path(__file__).resolve().parents[1]


def settings_for(tmp_path: Path) -> Settings:
    return load_settings(ROOT, {"TRIPBOT_DB": str(tmp_path / "trip.sqlite3")})


def plan(config: TripConfig) -> GroupRunPlan:
    window = config.windows[-1]
    return GroupRunPlan(
        window,
        tuple(
            RoundTripRequest(
                g.id,
                config.origins[g.origin].airports[0],
                "PEK",
                window.start - timedelta(days=2),
                window.end,
                g.headcount,
            )
            for g in config.groups
        ),
    )


def test_configured_group_plan(config: TripConfig, tmp_path: Path) -> None:
    expected = plan(config)
    validate_plan(config, expected)
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            [
                {
                    "group_id": r.group_id,
                    "origin": r.origin,
                    "destination": r.destination,
                    "outbound_date": r.outbound_date.isoformat(),
                    "return_date": r.return_date.isoformat(),
                }
                for r in expected.requests
            ]
        )
    )
    assert load_plan(path, config, expected.window.id) == expected
    assert [r.adults for r in expected.requests] == [1, 2, 2]


@pytest.mark.parametrize(
    "field", ["adults", "origin", "destination", "outbound_date", "return_date"]
)
def test_rejects_silent_config_changes(config: TripConfig, field: str) -> None:
    original = plan(config)
    request = original.requests[0]
    if field == "adults":
        request = replace(request, adults=2)
    elif field == "origin":
        request = replace(request, origin="MEX")
    elif field == "destination":
        request = replace(request, destination="HKG")
    elif field == "outbound_date":
        request = replace(request, outbound_date=request.outbound_date - timedelta(days=1))
    else:
        request = replace(request, return_date=request.return_date + timedelta(days=3))
    with pytest.raises(ValueError, match="config"):
        validate_plan(config, replace(original, requests=(request, *original.requests[1:])))


def test_requires_every_group(config: TripConfig) -> None:
    original = plan(config)
    with pytest.raises(ValueError, match="all configured"):
        validate_plan(config, replace(original, requests=original.requests[:1]))


def test_isolated_pipeline(config: TripConfig, reference: Reference, tmp_path: Path) -> None:
    original = plan(config)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(432))) as client:
        source = TripComFallback(TripCom(client), None, budget=3)
        with patch("time.sleep"):
            output = run_groups(config, reference, original, source, EmptyGoogle(), tmp_path)
    report = json.loads(output.read_text())
    assert len(report["results"]) == 12
    assert report["joint_ranking"]["combos"] == []
    with sqlite3.connect(tmp_path / "group-search.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 9
    with httpx.Client() as client, pytest.raises(ValueError, match="fresh output"):
        run_groups(
            config,
            reference,
            original,
            TripComFallback(TripCom(client), None),
            EmptyGoogle(),
            tmp_path,
        )


class EmptyGoogle:
    name = "google"
    shape = SearchShape(many_airports=True, city_codes=False)

    def search(self, key: SearchKey) -> SearchResult:
        return SearchResult("empty")


@pytest.mark.parametrize("enabled", [False, True])
def test_group_command_has_no_publish_side_effects(
    config: TripConfig, tmp_path: Path, enabled: bool
) -> None:
    settings = settings_for(tmp_path)
    with (
        patch("tripbot.cli.load_plan", return_value=plan(config)),
        patch("tripbot.cli.run_groups", return_value=tmp_path / "results.json") as run,
        patch("tripbot.cli.TripComBrowserSource") as browser,
        patch("tripbot.cli.publish") as publish,
        patch("tripbot.cli.ping") as ping,
        patch("tripbot.cli.notify_nightly") as notify,
    ):
        assert group_search_command(settings, tmp_path / "plan", tmp_path, "B", enabled, 3) == 0
        assert run.call_count == 1
        assert browser.call_count == int(enabled)
        publish.assert_not_called()
        ping.assert_not_called()
        notify.assert_not_called()


@pytest.mark.parametrize("budget", [-1, 0, 13])
def test_command_budget_checked_before_network(
    config: TripConfig, tmp_path: Path, budget: int
) -> None:
    settings = settings_for(tmp_path)
    with (
        patch("tripbot.cli.load_plan", return_value=plan(config)),
        patch("tripbot.cli.run_groups") as run,
        pytest.raises(ValueError, match="budget"),
    ):
        group_search_command(settings, tmp_path / "plan", tmp_path, "B", False, budget)
    run.assert_not_called()
