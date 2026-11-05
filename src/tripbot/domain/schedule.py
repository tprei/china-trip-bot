"""Calendar rules: the weekly summary and train ticket sale dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from tripbot.config import SummarySchedule
from tripbot.domain.routes import Route

TRAIN_SALE_DAYS_AHEAD = 14  # 12306 sells 15 days ahead, counting the day of sale


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def summary_due(now_local: datetime, schedule: SummarySchedule, last_week: str | None) -> bool:
    """True once the summary hour has passed this week and it has not gone out yet."""
    if week_key(now_local.date()) == last_week:
        return False
    if now_local.weekday() != schedule.weekday:
        return False
    return (now_local.hour, now_local.minute) >= (schedule.hour, schedule.minute)


@dataclass(frozen=True)
class SaleDate:
    opens: date
    travel_day: date
    origin: str
    destination: str
    train: str
    overnight: bool


def train_sale_dates(route: Route) -> list[SaleDate]:
    """When tickets for each train leg of the route go on sale."""
    return [
        SaleDate(
            opens=leg.day - timedelta(days=TRAIN_SALE_DAYS_AHEAD),
            travel_day=leg.day,
            origin=leg.origin,
            destination=leg.destination,
            train=leg.option.name,
            overnight=leg.option.overnight,
        )
        for leg in route.legs
        if leg.option.mode in ("train", "sleeper")
    ]
