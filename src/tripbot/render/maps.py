"""China's outline and the projection that places cities on it, for the route map."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

OUTLINE = Path(__file__).parent / "maps" / "china.yaml"


@dataclass(frozen=True)
class Outline:
    """An SVG path in pixels and the equirectangular projection it was drawn with."""

    west: float
    north: float
    lat0: float
    scale: float
    width: int
    height: int
    path: str

    def point(self, latitude: float, longitude: float) -> tuple[float, float]:
        x = (longitude - self.west) * math.cos(math.radians(self.lat0)) * self.scale
        y = (self.north - latitude) * self.scale
        return round(x, 1), round(y, 1)


@cache
def china() -> Outline:
    raw = yaml.safe_load(OUTLINE.read_text(encoding="utf-8"))
    return Outline(
        west=float(raw["west"]),
        north=float(raw["north"]),
        lat0=float(raw["lat0"]),
        scale=float(raw["scale"]),
        width=int(raw["width"]),
        height=int(raw["height"]),
        path=str(raw["path"]),
    )
