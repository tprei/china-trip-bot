from dataclasses import replace
from datetime import datetime, timedelta

from tripbot.config import TripConfig
from tripbot.domain.searches import SourcePlan, all_keys, plan_tonight
from tripbot.pipeline.state import search_space
from tripbot.reference import Reference

GOOGLE = SourcePlan(name="google", many_airports=False, city_codes=False, budget=150)
SERPAPI = SourcePlan(name="serpapi", many_airports=True, city_codes=False, budget=8)
TRIPCOM = SourcePlan(name="tripcom", many_airports=False, city_codes=True, budget=12)


def test_key_counts_follow_each_source_shape(config: TripConfig, reference: Reference) -> None:
    space = search_space(config, reference)
    # 4 home airports × 13 China airports × 2 directions × 15 dates (3 windows × 5 slid days)
    assert len(all_keys(GOOGLE, space)) == 4 * 13 * 2 * 15
    # 3 groups × 2 directions × 15 dates, every airport in one search
    assert len(all_keys(SERPAPI, space)) == 3 * 2 * 15
    # 3 groups × 2 directions × 15 dates × 11 Trip.com city codes
    assert len(all_keys(TRIPCOM, space)) == 3 * 2 * 15 * 11


def test_return_keys_fly_from_china_home(config: TripConfig, reference: Reference) -> None:
    space = search_space(config, reference)
    returns = [k for k in all_keys(SERPAPI, space) if k.direction == "return"]
    sao = next(k for k in returns if k.group_id == "sao")
    assert sao.destinations == ("GRU",)
    assert "PEK" in sao.origins


def test_plan_respects_budget_focus_and_staleness(config: TripConfig, reference: Reference) -> None:
    space = search_space(config, reference)
    keys = all_keys(TRIPCOM, space)
    now = datetime(2026, 11, 6, 3, 0)
    last = {key: now - timedelta(days=1) for key in keys}
    stalest = keys[-1]
    last[stalest] = now - timedelta(days=30)

    source = replace(TRIPCOM, budget=17)
    tonight = plan_tonight(source, space, last, focus={("lon", "outbound", "guangzhou")})

    assert len(tonight) == 17
    assert all(k.group_id == "lon" and k.destinations == ("CAN",) for k in tonight[:15])
    assert tonight[15] == stalest


def test_never_searched_keys_come_first(config: TripConfig, reference: Reference) -> None:
    space = search_space(config, reference)
    keys = all_keys(SERPAPI, space)
    now = datetime(2026, 11, 6, 3, 0)
    last = dict.fromkeys(keys[1:], now)
    assert plan_tonight(SERPAPI, space, last, focus=set())[0] == keys[0]
