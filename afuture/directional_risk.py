"""Causal risk scaling for the execution-aligned directional portfolio."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import stdev
from typing import Iterable


@dataclass(frozen=True)
class DirectionalRiskGovernor:
    """Scale gross risk from completed account returns only.

    The governor can only reduce the frozen policy's target gross. It never increases
    leverage and intentionally uses a two-completed-day sample so current-session PnL
    cannot leak into the next target decision.
    """

    lookback_days: int = 2
    volatility_trigger: float = 0.03
    loss_trigger: float = 0.03
    defensive_scale: float = 0.25

    def __post_init__(self) -> None:
        if self.lookback_days < 2:
            raise ValueError("directional risk lookback_days must be >= 2")
        if self.volatility_trigger <= 0:
            raise ValueError("directional volatility_trigger must be positive")
        if self.loss_trigger <= 0:
            raise ValueError("directional loss_trigger must be positive")
        if not 0 < self.defensive_scale <= 1:
            raise ValueError("directional defensive_scale must be in (0, 1]")

    def scale(self, completed_returns: Iterable[float]) -> float:
        values = [
            float(value)
            for value in completed_returns
            if isfinite(float(value))
        ]
        if values and values[-1] <= -self.loss_trigger:
            return self.defensive_scale
        sample = values[-self.lookback_days :]
        if (
            len(sample) >= self.lookback_days
            and stdev(sample) >= self.volatility_trigger
        ):
            return self.defensive_scale
        return 1.0
