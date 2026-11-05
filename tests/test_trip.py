from datetime import date

from tripbot.domain.trip import Flex, Window

WINDOW_B = Window(id="B", start=date(2027, 8, 30), end=date(2027, 9, 10))
FLEX = Flex(outbound_days_before=2, return_days_after=2)


def test_outbound_dates_end_on_the_first_day() -> None:
    assert WINDOW_B.outbound_dates(FLEX) == [
        date(2027, 8, 28),
        date(2027, 8, 29),
        date(2027, 8, 30),
    ]


def test_return_dates_start_on_the_last_day() -> None:
    assert WINDOW_B.return_dates(FLEX) == [date(2027, 9, 10), date(2027, 9, 11), date(2027, 9, 12)]


def test_nights_and_label() -> None:
    assert WINDOW_B.nights == 11
    assert WINDOW_B.label() == "30 Aug – 10 Sep"
    assert Window(id="C", start=date(2027, 8, 9), end=date(2027, 8, 20)).label() == "9 – 20 Aug"
