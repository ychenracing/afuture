"""Deterministic production-mechanics proxy for the frozen directional portfolio.

This module deliberately does not search Alpha or parameters.  It translates frozen
product weights into integer contract lots and applies the same account-style hard gates
used by production. Historical broker margin schedules are unavailable, so margin is
explicitly a conservative proxy assumption rather than claimed exact CTP history.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Mapping

import pandas as pd

from .directional import RebalancePlan


PRODUCT_MULTIPLIERS: dict[str, float] = {
    "A": 10.0,
    "B": 10.0,
    "C": 10.0,
    "CS": 10.0,
    "M": 10.0,
    "P": 10.0,
    "Y": 10.0,
    "OI": 10.0,
    "RM": 10.0,
    "SR": 10.0,
    "TA": 10.0,
    "MA": 10.0,
    "AG": 15.0,
    "AL": 5.0,
    "CU": 5.0,
    "PB": 5.0,
    "ZN": 5.0,
    "AU": 1000.0,
    "AP": 10.0,
    "BC": 5.0,
    "BU": 10.0,
    "FU": 10.0,
    "HC": 10.0,
    "RB": 10.0,
    "RU": 10.0,
    "SP": 10.0,
    "CF": 5.0,
    "CJ": 5.0,
    "EB": 5.0,
    "EG": 10.0,
    "FG": 20.0,
    "I": 100.0,
    "J": 100.0,
    "JM": 60.0,
    "L": 5.0,
    "PP": 5.0,
    "V": 5.0,
    "PF": 5.0,
    "PK": 5.0,
    "SF": 5.0,
    "SM": 5.0,
    "SS": 5.0,
    "LH": 16.0,
    "LU": 10.0,
    "NR": 10.0,
    "NI": 1.0,
    "SN": 1.0,
    "PG": 20.0,
    "SA": 20.0,
    "UR": 20.0,
}


@dataclass(frozen=True)
class ProductionMechanicsConfig:
    initial_capital: float = 500000.0
    margin_rate_proxy: float = 0.12
    margin_estimate_buffer: float = 1.25
    max_margin_ratio: float = 0.35
    min_available_ratio: float = 0.25
    max_contract_volume: int = 100
    max_daily_loss_ratio: float = 0.05
    max_total_drawdown_ratio: float = 0.30
    min_days_to_delivery: int = 20
    min_volume: float = 1000.0
    min_open_interest: float = 5000.0

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0 < self.margin_rate_proxy < 1:
            raise ValueError("margin_rate_proxy must be in (0, 1)")
        if self.margin_estimate_buffer < 1:
            raise ValueError("margin_estimate_buffer must be >= 1")
        if not 0 < self.max_margin_ratio < 1:
            raise ValueError("max_margin_ratio must be in (0, 1)")
        if not 0 <= self.min_available_ratio < 1:
            raise ValueError("min_available_ratio must be in [0, 1)")
        if self.max_contract_volume <= 0:
            raise ValueError("max_contract_volume must be positive")
        if not 0 < self.max_daily_loss_ratio < 1:
            raise ValueError("max_daily_loss_ratio must be in (0, 1)")
        if not 0 < self.max_total_drawdown_ratio < 1:
            raise ValueError("max_total_drawdown_ratio must be in (0, 1)")


class DirectionalProductionAcceptance:
    """Pure production-mechanics primitives shared by unit tests and L4 proxy tooling."""

    def __init__(self, config: ProductionMechanicsConfig | None = None) -> None:
        self.config = config or ProductionMechanicsConfig()
        self.config.validate()

    @staticmethod
    def _product(symbol: str) -> str:
        value = str(symbol).upper()
        prefix = "".join(char for char in value if char.isalpha())
        if prefix not in PRODUCT_MULTIPLIERS:
            raise ValueError(f"unknown frozen product multiplier: {symbol}")
        return prefix

    def target_lots(
        self,
        *,
        equity: float,
        product_weights: Mapping[str, float],
        product_open_prices: Mapping[str, float],
        selected_symbols: Mapping[str, str],
    ) -> dict[str, int]:
        if equity <= 0:
            return {}
        result: dict[str, int] = {}
        for raw_product, raw_weight in sorted(product_weights.items()):
            product = str(raw_product).upper()
            weight = float(raw_weight)
            if abs(weight) <= 1e-15:
                continue
            symbol = selected_symbols.get(product)
            price = float(product_open_prices.get(product, 0.0))
            multiplier = PRODUCT_MULTIPLIERS.get(product)
            if symbol is None or multiplier is None or price <= 0:
                continue
            lots = min(
                self.config.max_contract_volume,
                floor(equity * abs(weight) / (price * multiplier)),
            )
            if lots > 0:
                result[str(symbol)] = lots if weight > 0 else -lots
        return result

    @staticmethod
    def rebalance_plan(
        *, current_lots: Mapping[str, int], target_lots: Mapping[str, int]
    ) -> RebalancePlan:
        reductions: dict[str, int] = {}
        openings: dict[str, int] = {}
        for symbol in sorted(set(current_lots) | set(target_lots)):
            have = int(current_lots.get(symbol, 0))
            target = int(target_lots.get(symbol, 0))
            if have == target:
                continue
            if have == 0:
                if target:
                    openings[symbol] = target
                continue
            if target == 0 or (have > 0) != (target > 0):
                reductions[symbol] = -have
                continue
            if abs(target) < abs(have):
                reductions[symbol] = target - have
            elif abs(target) > abs(have):
                openings[symbol] = target - have
        return RebalancePlan(
            reductions=reductions,
            openings={} if reductions else openings,
        )

    def check_opening_batch(
        self,
        *,
        equity: float,
        current_margin: float,
        current_lots: Mapping[str, int],
        openings: Mapping[str, int],
        open_prices: Mapping[str, float],
    ) -> tuple[bool, str, float]:
        if equity <= 0:
            return False, "equity is not positive", 0.0
        estimated = 0.0
        for symbol, delta in openings.items():
            requested = abs(int(delta))
            if requested <= 0:
                continue
            existing = abs(int(current_lots.get(symbol, 0)))
            if existing + requested > self.config.max_contract_volume:
                return False, "contract volume limit reached", estimated
            price = float(open_prices.get(symbol, 0.0))
            if price <= 0:
                return False, f"missing opening price: {symbol}", estimated
            multiplier = PRODUCT_MULTIPLIERS[self._product(symbol)]
            estimated += (
                price
                * multiplier
                * requested
                * self.config.margin_rate_proxy
                * self.config.margin_estimate_buffer
            )
        post_margin = float(current_margin) + estimated
        if post_margin / equity > self.config.max_margin_ratio:
            return False, "combined margin ratio would exceed limit", float(estimated)
        if (equity - post_margin) / equity < self.config.min_available_ratio:
            return False, "combined cash reserve would fall below limit", float(estimated)
        return True, "", float(estimated)

    def account_risk_reason(
        self,
        *,
        equity: float,
        day_start_equity: float,
        high_watermark: float,
    ) -> str:
        if equity <= 0 or day_start_equity <= 0 or high_watermark <= 0:
            return "equity is not positive"
        daily_loss = max(0.0, day_start_equity - equity) / day_start_equity
        drawdown = max(0.0, high_watermark - equity) / high_watermark
        if daily_loss + 1e-12 >= self.config.max_daily_loss_ratio:
            return "daily loss limit reached"
        if drawdown + 1e-12 >= self.config.max_total_drawdown_ratio:
            return "drawdown limit reached"
        return ""

    def select_contracts_for_day(
        self, raw: pd.DataFrame, target_day: pd.Timestamp
    ) -> dict[str, str]:
        frame = raw.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["delivery"] = pd.to_datetime(frame["delivery"], errors="coerce")
        frame["product"] = frame["product"].astype(str).str.upper()
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for column in ("volume", "hold"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(
            subset=["date", "delivery", "product", "symbol", "volume", "hold"]
        )
        day = pd.Timestamp(target_day).normalize()
        prior_dates = frame.loc[frame["date"] < day, "date"]
        if prior_dates.empty:
            return {}
        completed = pd.Timestamp(prior_dates.max()).normalize()
        snapshot = frame[frame["date"].dt.normalize() == completed].copy()
        snapshot = snapshot[
            (snapshot["delivery"] - day).dt.days >= self.config.min_days_to_delivery
        ]
        snapshot = snapshot[
            (snapshot["volume"] >= self.config.min_volume)
            & (snapshot["hold"] >= self.config.min_open_interest)
        ]
        result: dict[str, str] = {}
        for product, rows in snapshot.groupby("product"):
            rows = rows.sort_values(
                ["hold", "volume", "delivery", "symbol"],
                ascending=[False, False, True, True],
            )
            if not rows.empty:
                result[str(product)] = str(rows.iloc[0]["symbol"])
        return result
