"""Entry rules for the group, as last checked."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class VisaInfo:
    as_of: date
    passport: str
    status: str
    visa_free_until: date
    decide_by: date
    facts: tuple[str, ...]
    steps: tuple[str, ...]
    sources: tuple[str, ...]
