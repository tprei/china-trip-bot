"""When a price change is worth an instant message."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tripbot.config import AlertRules
from tripbot.domain.money import Money

AlertKind = Literal["record_low", "drop"]


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    window_id: str
    total: Money
    previous: Money  # the record beaten, or the total in the last summary

    @property
    def saving(self) -> Money:
        return self.previous - self.total

    @property
    def saving_pct(self) -> float:
        return round(self.saving.minor * 100 / self.previous.minor, 1)


def decide_alerts(
    totals: Mapping[str, Money],
    history: Mapping[str, Sequence[Money]],
    summary_totals: Mapping[str, Money] | None,
    rules: AlertRules,
    alerted_today: set[str],
) -> list[Alert]:
    """At most one alert per window per day; a new record low beats a plain drop.

    `totals` are tonight's best totals; `history` the earlier ones (not tonight's).
    """
    alerts: list[Alert] = []
    for window_id, total in totals.items():
        if window_id in alerted_today:
            continue
        earlier = history.get(window_id, ())
        if rules.record_low and earlier and total < min(earlier):
            alerts.append(Alert("record_low", window_id, total, min(earlier)))
            continue
        reference = (summary_totals or {}).get(window_id)
        if reference is not None and _dropped(total, reference, rules.drop_pct):
            alerts.append(Alert("drop", window_id, total, reference))
    return alerts


def _dropped(total: Money, reference: Money, drop_pct: int) -> bool:
    return total.minor * 100 <= reference.minor * (100 - drop_pct)
