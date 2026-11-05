"""Climate risk per region and month, used to weigh nights at each stop."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Typhoon = Literal["low", "moderate", "high"]

TYPHOON_BONUS: dict[Typhoon, float] = {"low": 0.0, "moderate": 0.15, "high": 0.3}
RAIN_FOR_FULL_RISK_MM = 250


@dataclass(frozen=True)
class MonthClimate:
    rain_mm: int
    typhoon: Typhoon

    @property
    def risk(self) -> float:
        return min(1.0, self.rain_mm / RAIN_FOR_FULL_RISK_MM + TYPHOON_BONUS[self.typhoon])


@dataclass(frozen=True)
class Climate:
    regions: Mapping[str, Mapping[int, MonthClimate]]

    def month(self, region: str, month: int) -> MonthClimate | None:
        return self.regions.get(region, {}).get(month)

    def risk(self, region: str, month: int) -> float:
        """0 (dry) to 1 (wet, typhoon season). Unknown regions count as no risk."""
        climate = self.month(region, month)
        return climate.risk if climate else 0.0


def risk_label(risk: float) -> str:
    if risk >= 0.7:
        return "high"
    if risk >= 0.4:
        return "moderate"
    return "low"
