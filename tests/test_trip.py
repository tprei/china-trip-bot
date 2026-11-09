from datetime import date, timedelta

from tripbot.domain.trip import Flex, Window

WINDOW_B = Window(id="B", start=date(2027, 8, 30), end=date(2027, 9, 10))
FLEX = Flex(outbound_days_before=2, return_days_after=2, shift_days=1, weekday_return_eur=100)


def test_slides_keep_the_nights_and_widen_the_dates() -> None:
    slides = WINDOW_B.variants(FLEX)
    assert [slide.start for slide in slides] == [
        date(2027, 8, 29),
        date(2027, 8, 30),
        date(2027, 8, 31),
    ]
    assert {(slide.id, slide.nights) for slide in slides} == {("B", 11)}
    outbound = sorted({day for slide in slides for day in slide.outbound_dates(FLEX)})
    back = sorted({day for slide in slides for day in slide.return_dates(FLEX)})
    assert outbound == [date(2027, 8, 27) + timedelta(days=n) for n in range(5)]
    assert back == [date(2027, 9, 9) + timedelta(days=n) for n in range(5)]


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
