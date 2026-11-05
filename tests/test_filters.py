from datetime import datetime

from factories import eur, offer

from tripbot.config import TripConfig
from tripbot.domain.filters import rejection

DEPART = datetime(2027, 8, 28, 10, 0)


def check(
    config: TripConfig,
    path: list[str],
    *,
    layover_hours: int = 2,
    stops_on_board: tuple[str, ...] = (),
    self_transfer: bool = False,
) -> str | None:
    found = offer(
        "lon",
        "outbound",
        path,
        DEPART,
        eur(600),
        layover_hours=layover_hours,
        stops_on_board=stops_on_board,
        self_transfer=self_transfer,
    )
    return rejection(found, config.flights, config.china_airports)


def test_a_direct_flight_to_china_passes(config: TripConfig) -> None:
    assert check(config, ["LHR", "PEK"]) is None


def test_two_stops_are_out(config: TripConfig) -> None:
    assert check(config, ["LHR", "IST", "DOH", "PEK"]) == "2 stops"


def test_a_same_plane_stop_counts_as_a_stop(config: TripConfig) -> None:
    assert check(config, ["GRU", "MAD", "PEK"], stops_on_board=("MAD",)) == "2 stops"


def test_jordan_and_russia_are_blocked(config: TripConfig) -> None:
    assert check(config, ["LHR", "AMM", "PEK"]) == "stops in AMM"
    assert check(config, ["LHR", "SVO", "PEK"]) == "stops in SVO"


def test_long_layovers_are_out(config: TripConfig) -> None:
    assert check(config, ["LHR", "DOH", "PEK"], layover_hours=10) == "10h layover in DOH"


def test_self_transfers_are_out(config: TripConfig) -> None:
    assert check(config, ["DUB", "BCN", "PVG"], self_transfer=True) == (
        "self-transfer on separate tickets"
    )


def test_hong_kong_is_not_mainland(config: TripConfig) -> None:
    assert check(config, ["LHR", "HKG"]) == "HKG is not a mainland China airport"
