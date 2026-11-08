from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tripbot.config import FlightRules, TripConfig
from tripbot.domain.flights import Segment
from tripbot.domain.money import Money
from tripbot.domain.ranking import RankingInputs
from tripbot.domain.roundtrip import (
    Journey,
    RoundTripQuote,
    RoundTripRequest,
    RoundTripResult,
    rank_group_quotes,
    rank_roundtrips,
)
from tripbot.domain.trip import Group
from tripbot.reference import Reference, ranking_inputs

OUT = datetime(2027, 8, 29, 9)
BACK = datetime(2027, 9, 12, 12)
RULES = FlightRules(1, 360, frozenset({"HKG"}), False)


def journey(origin: str, destination: str, depart: datetime) -> Journey:
    return Journey(
        (
            Segment(
                origin,
                destination,
                depart,
                depart + timedelta(hours=12),
                720,
                "CA",
                "Air China",
                "CA123",
            ),
        ),
        720,
    )


def quote(group: Group, destination: str = "PEK", price: int = 10000) -> RoundTripQuote:
    return RoundTripQuote(
        RoundTripRequest(group.id, "DUB", destination, OUT.date(), BACK.date(), group.headcount),
        journey("DUB", destination, OUT),
        journey(destination, "DUB", BACK),
        Money(price * group.headcount + 1, "EUR"),
        Money(price, "EUR"),
        datetime(2026, 11, 7),
        "fixture",
        None,
        "Observed separately",
    )


@pytest.fixture
def ranking(config: TripConfig, reference: Reference) -> RankingInputs:
    return ranking_inputs(config, reference, config.fallback_rates)


def test_observed_prices_are_not_reconstructed() -> None:
    q = quote(Group("g", "dub", ("A", "B")))
    assert q.total == Money(20001, "EUR")
    assert q.per_person == Money(10000, "EUR")
    assert not hasattr(q.outbound, "price")
    assert q.outbound.stops == 0
    assert q.outbound.via == ()
    assert q.outbound.layovers == []
    assert "Air China" in q.outbound.summary()
    assert RoundTripResult("ok", (q,)).quotes == (q,)
    assert RoundTripResult("empty").quotes == ()
    assert RoundTripResult("error", error="blocked").error == "blocked"


@pytest.mark.parametrize("adults", [0, -1, True])
def test_invalid_passengers(adults: int) -> None:
    with pytest.raises(ValueError, match="adults"):
        RoundTripRequest("g", "DUB", "PEK", OUT.date(), BACK.date(), adults)


def test_quote_validation() -> None:
    q = quote(Group("g", "dub", ("A",)))
    with pytest.raises(ValueError, match="currencies"):
        replace(q, total=Money(10001, "USD"))
    with pytest.raises(ValueError, match="positive"):
        replace(q, per_person=Money(0, "EUR"))
    with pytest.raises(ValueError, match="inbound"):
        replace(q, inbound=journey("CAN", "DUB", BACK))
    with pytest.raises(ValueError, match="dates"):
        replace(q, request=replace(q.request, return_date=BACK.date() + timedelta(days=1)))
    with pytest.raises(ValueError, match="segments"):
        Journey((), 10)
    with pytest.raises(ValueError, match="only ok"):
        RoundTripResult("ok")
    with pytest.raises(ValueError, match="only error"):
        RoundTripResult("error")


def test_only_matched_quotes_and_observed_totals_rank(
    config: TripConfig,
    ranking: RankingInputs,
) -> None:
    quotes = [quote(g, city) for g in ranking.groups for city in ("PEK", "CAN")]
    result = rank_roundtrips(config.windows[2], quotes, config.route.stops, ranking, RULES)
    assert len(result.combos) == 2  # never synthesize PEK/CAN open jaws
    for combo in result.combos:
        assert combo.entry == combo.exit
        assert combo.flights_eur == Money(sum(q.total.minor for q in quotes) // 2, "EUR")
        assert combo.total_eur == combo.flights_eur + combo.internal_eur
        assert combo.score_eur == combo.flights_eur + combo.route.score_eur.times(5)
        assert combo.choice("missing") is None
        assert all(c.quote in quotes for c in combo.choices)


def test_all_quote_options_survive_and_fairness_filters(
    config: TripConfig,
    ranking: RankingInputs,
) -> None:
    ranking = replace(ranking, groups=ranking.groups[:1])
    cheap = quote(ranking.groups[0])
    expensive = quote(ranking.groups[0], price=50000)
    slow = replace(cheap, outbound=replace(cheap.outbound, total_minutes=10000))
    result = rank_roundtrips(
        config.windows[2], [cheap, expensive, slow], config.route.stops, ranking, RULES
    )
    assert len(result.combos) == 3
    assert result.best is not None and result.fairest is not None
    assert result.best.choices[0].quote == cheap
    assert sum(c.within_rule for c in result.combos) == 1
    assert result.best.choice(cheap.request.group_id) == result.best.choices[0]


@pytest.mark.parametrize("bad", ["pax", "self_transfer", "blocked", "late", "return"])
def test_hard_filters_apply_before_ranking(
    bad: str,
    config: TripConfig,
    ranking: RankingInputs,
) -> None:
    quotes = [quote(g) for g in ranking.groups]
    q = quotes[0]
    if bad == "pax":
        q = replace(q, request=replace(q.request, adults=10))
    elif bad == "self_transfer":
        q = replace(q, inbound=replace(q.inbound, self_transfer=True))
    elif bad == "blocked":
        segment = replace(q.inbound.segments[0], stops_on_board=("HKG",))
        q = replace(q, inbound=replace(q.inbound, segments=(segment,)))
    elif bad == "late":
        segment = replace(q.outbound.segments[0], arrive=datetime(2027, 8, 31))
        q = replace(q, outbound=replace(q.outbound, segments=(segment,)))
    else:
        depart = BACK + timedelta(days=30)
        q = replace(
            q,
            request=replace(q.request, return_date=depart.date()),
            inbound=journey("PEK", "DUB", depart),
        )
    quotes[0] = q
    result = rank_roundtrips(config.windows[2], quotes, config.route.stops, ranking, RULES)
    assert result.combos == ()
    assert result.best is None and result.fairest is None


def test_no_shared_city_or_missing_group(config: TripConfig, ranking: RankingInputs) -> None:
    quotes = [quote(g, "PEK" if i else "CAN") for i, g in enumerate(ranking.groups)]
    for candidates in (quotes, quotes[1:], []):
        result = rank_roundtrips(config.windows[2], candidates, config.route.stops, ranking, RULES)
        assert result.combos == ()


def test_group_ordering_preserves_whole_quotes(ranking: RankingInputs, config: TripConfig) -> None:
    group = config.groups[0]
    cheap, costly = quote(group, price=10000), quote(group, price=20000)
    assert rank_group_quotes(
        config.windows[-1], [costly, cheap], group, ranking, config.flights
    ) == (
        cheap,
        costly,
    )
