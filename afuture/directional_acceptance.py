"""Deterministic production-mechanics proxy for the frozen directional portfolio.

This module deliberately does not search Alpha or parameters. It translates frozen
product weights into integer contract lots and applies production-style account hard
gates. Historical broker margin schedules are unavailable, so margin is explicitly a
proxy assumption rather than claimed exact CTP history.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Mapping

import pandas as pd

from .directional import RebalancePlan
from .directional_risk import DirectionalRiskGovernor


PRODUCT_MULTIPLIERS: dict[str, float] = {
    "A": 10.0, "B": 10.0, "C": 10.0, "CS": 10.0, "M": 10.0,
    "P": 10.0, "Y": 10.0, "OI": 10.0, "RM": 10.0, "SR": 10.0,
    "TA": 10.0, "MA": 10.0, "AG": 15.0, "AL": 5.0, "CU": 5.0,
    "PB": 5.0, "ZN": 5.0, "AU": 1000.0, "AP": 10.0, "BC": 5.0,
    "BU": 10.0, "FU": 10.0, "HC": 10.0, "RB": 10.0, "RU": 10.0,
    "SP": 10.0, "CF": 5.0, "CJ": 5.0, "EB": 5.0, "EG": 10.0,
    "FG": 20.0, "I": 100.0, "J": 100.0, "JM": 60.0, "L": 5.0,
    "PP": 5.0, "V": 5.0, "PF": 5.0, "PK": 5.0, "SF": 5.0,
    "SM": 5.0, "SS": 5.0, "LH": 16.0, "LU": 10.0, "NR": 10.0,
    "NI": 1.0, "SN": 1.0, "PG": 20.0, "SA": 20.0, "UR": 20.0,
}


@dataclass(frozen=True)
class ProductionMechanicsConfig:
    initial_capital: float = 500000.0
    margin_rate_proxy: float = 0.12
    margin_estimate_buffer: float = 1.25
    max_margin_ratio: float = 0.35
    min_available_ratio: float = 0.25
    max_contract_volume: int = 35
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


@dataclass(frozen=True)
class ProductionSimulationResult:
    daily: pd.DataFrame
    final_equity: float
    first_divergence: str = ""


class DirectionalProductionAcceptance:
    """Pure deterministic mechanics used by tests and the final L4 proxy tool."""

    def __init__(self, config: ProductionMechanicsConfig | None = None) -> None:
        self.config = config or ProductionMechanicsConfig()
        self.config.validate()
        self.risk_governor = DirectionalRiskGovernor()

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
        margin: float = 0.0,
    ) -> str:
        if equity <= 0 or day_start_equity <= 0 or high_watermark <= 0:
            return "equity is not positive"
        daily_loss = max(0.0, day_start_equity - equity) / day_start_equity
        drawdown = max(0.0, high_watermark - equity) / high_watermark
        margin_ratio = max(0.0, float(margin)) / equity
        available_ratio = (equity - max(0.0, float(margin))) / equity
        if daily_loss + 1e-12 >= self.config.max_daily_loss_ratio:
            return "daily loss limit reached"
        if drawdown + 1e-12 >= self.config.max_total_drawdown_ratio:
            return "drawdown limit reached"
        if margin_ratio > self.config.max_margin_ratio:
            return "margin ratio limit reached"
        if available_ratio < self.config.min_available_ratio:
            return "available cash reserve too low"
        return ""

    @staticmethod
    def _normalize_contracts(raw: pd.DataFrame) -> pd.DataFrame:
        frame = raw.copy()
        frame["date"] = pd.to_datetime(
            frame["date"], errors="coerce"
        ).dt.normalize()
        frame["delivery"] = pd.to_datetime(
            frame["delivery"], errors="coerce"
        )
        frame["product"] = frame["product"].astype(str).str.upper()
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for column in ("open", "close", "volume", "hold"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(
            subset=[
                "date", "delivery", "product", "symbol",
                "open", "close", "volume", "hold",
            ]
        )
        return frame[
            (frame["open"] > 0) & (frame["close"] > 0)
        ].copy()

    def select_contracts_for_day(
        self, raw: pd.DataFrame, target_day: pd.Timestamp
    ) -> dict[str, str]:
        frame = self._normalize_contracts(raw)
        day = pd.Timestamp(target_day).normalize()
        prior_dates = frame.loc[frame["date"] < day, "date"]
        if prior_dates.empty:
            return {}
        completed = pd.Timestamp(prior_dates.max()).normalize()
        snapshot = frame[frame["date"] == completed].copy()
        snapshot = snapshot[
            (snapshot["delivery"] - day).dt.days
            >= self.config.min_days_to_delivery
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

    def _margin(
        self,
        lots: Mapping[str, int],
        prices: Mapping[str, float],
    ) -> float:
        return float(
            sum(
                abs(int(volume))
                * float(prices.get(symbol, 0.0))
                * PRODUCT_MULTIPLIERS[self._product(symbol)]
                * self.config.margin_rate_proxy
                * self.config.margin_estimate_buffer
                for symbol, volume in lots.items()
            )
        )

    @staticmethod
    def _apply_deltas(
        lots: dict[str, int], deltas: Mapping[str, int]
    ) -> None:
        for symbol, delta in deltas.items():
            lots[symbol] = int(lots.get(symbol, 0)) + int(delta)
            if lots[symbol] == 0:
                lots.pop(symbol, None)

    def _turnover(
        self,
        deltas: Mapping[str, int],
        prices: Mapping[str, float],
    ) -> float:
        return float(
            sum(
                abs(int(delta))
                * float(prices.get(symbol, 0.0))
                * PRODUCT_MULTIPLIERS[self._product(symbol)]
                for symbol, delta in deltas.items()
            )
        )

    def simulate(
        self,
        raw: pd.DataFrame,
        weights: pd.DataFrame,
        *,
        cost_bps: float,
    ) -> ProductionSimulationResult:
        frame = self._normalize_contracts(raw)
        weight_frame = weights.copy()
        weight_frame.index = pd.to_datetime(
            weight_frame.index, errors="coerce"
        ).normalize()
        weight_frame = weight_frame[
            ~weight_frame.index.isna()
        ].sort_index().fillna(0.0)
        weight_frame.columns = [
            str(column).upper() for column in weight_frame.columns
        ]
        if bool(
            (weight_frame.abs().sum(axis=1) > 2.0 + 1e-10).any()
        ):
            raise ValueError("production mechanics weights exceed 2x gross")

        by_day_symbol = {
            (pd.Timestamp(day).normalize(), str(symbol)): group.iloc[-1]
            for (day, symbol), group in frame.groupby(
                ["date", "symbol"], sort=False
            )
        }
        equity = float(self.config.initial_capital)
        high_watermark = equity
        lots: dict[str, int] = {}
        previous_close: dict[str, float] = {}
        completed_returns: list[float] = []
        halted = False
        first_divergence = ""
        output_rows: list[dict] = []
        cost_rate = float(cost_bps) / 10000.0

        for day, weight_row in weight_frame.iterrows():
            day = pd.Timestamp(day).normalize()
            previous_equity = equity
            day_start_equity = previous_equity
            turnover_notional = 0.0
            risk_reason = ""
            margin_reject = ""
            daily_circuit = False
            risk_scale = self.risk_governor.scale(completed_returns)

            if halted:
                output_rows.append(
                    {
                        "date": day,
                        "equity": equity,
                        "daily_return": 0.0,
                        "turnover_notional": 0.0,
                        "gross_notional": 0.0,
                        "margin": 0.0,
                        "risk_reason": first_divergence,
                        "margin_reject": "",
                        "daily_circuit": False,
                        "risk_scale": risk_scale,
                        "halted": True,
                    }
                )
                continue

            open_prices: dict[str, float] = {}
            close_prices: dict[str, float] = {}
            missing_existing = False
            for symbol, volume in list(lots.items()):
                row = by_day_symbol.get((day, symbol))
                if row is None or symbol not in previous_close:
                    risk_reason = f"missing same-contract next price: {symbol}"
                    first_divergence = first_divergence or risk_reason
                    missing_existing = True
                    break
                open_price = float(row["open"])
                close_price = float(row["close"])
                open_prices[symbol] = open_price
                close_prices[symbol] = close_price
                equity += (
                    open_price - float(previous_close[symbol])
                ) * int(volume) * PRODUCT_MULTIPLIERS[self._product(symbol)]
            if missing_existing:
                halted = True
                output_rows.append(
                    {
                        "date": day,
                        "equity": equity,
                        "daily_return": equity / previous_equity - 1.0,
                        "turnover_notional": 0.0,
                        "gross_notional": 0.0,
                        "margin": 0.0,
                        "risk_reason": risk_reason,
                        "margin_reject": "",
                        "daily_circuit": False,
                        "risk_scale": risk_scale,
                        "halted": True,
                    }
                )
                continue

            # Production observes account equity/margin at the session open before
            # normal target rebalance. A favorable gap also establishes a new HWM.
            high_watermark = max(high_watermark, equity)
            current_margin = self._margin(lots, open_prices) if lots else 0.0
            risk_reason = self.account_risk_reason(
                equity=equity,
                day_start_equity=day_start_equity,
                high_watermark=high_watermark,
                margin=current_margin,
            )
            if risk_reason:
                first_divergence = first_divergence or risk_reason
                if lots:
                    closing = {symbol: -volume for symbol, volume in lots.items()}
                    close_turnover = self._turnover(closing, open_prices)
                    turnover_notional += close_turnover
                    equity -= close_turnover * cost_rate
                    lots.clear()
                if risk_reason == "daily loss limit reached":
                    daily_circuit = True
                else:
                    halted = True

            if not halted and not daily_circuit:
                selected = self.select_contracts_for_day(frame, day)
                product_open: dict[str, float] = {}
                selected_symbols: dict[str, str] = {}
                for product, symbol in selected.items():
                    row = by_day_symbol.get((day, symbol))
                    if row is None:
                        continue
                    selected_symbols[product] = symbol
                    product_open[product] = float(row["open"])
                    open_prices[symbol] = float(row["open"])
                    close_prices[symbol] = float(row["close"])

                product_weights = {
                    str(product).upper(): float(value) * risk_scale
                    for product, value in weight_row.items()
                }
                target = self.target_lots(
                    equity=equity,
                    product_weights=product_weights,
                    product_open_prices=product_open,
                    selected_symbols=selected_symbols,
                )
                required_products = {
                    product
                    for product, value in product_weights.items()
                    if abs(value) > 1e-15
                }
                unavailable_products = required_products - set(selected_symbols)
                for symbol, volume in lots.items():
                    if self._product(symbol) in unavailable_products:
                        target[symbol] = int(volume)

                phase = self.rebalance_plan(
                    current_lots=lots,
                    target_lots=target,
                )
                if phase.reductions:
                    reduction_turnover = self._turnover(
                        phase.reductions, open_prices
                    )
                    turnover_notional += reduction_turnover
                    equity -= reduction_turnover * cost_rate
                    self._apply_deltas(lots, phase.reductions)

                phase = self.rebalance_plan(
                    current_lots=lots,
                    target_lots=target,
                )
                if phase.openings:
                    current_margin = self._margin(lots, open_prices)
                    allowed, margin_reject, _ = self.check_opening_batch(
                        equity=equity,
                        current_margin=current_margin,
                        current_lots=lots,
                        openings=phase.openings,
                        open_prices=open_prices,
                    )
                    if allowed:
                        opening_turnover = self._turnover(
                            phase.openings, open_prices
                        )
                        turnover_notional += opening_turnover
                        equity -= opening_turnover * cost_rate
                        self._apply_deltas(lots, phase.openings)
                    else:
                        first_divergence = first_divergence or margin_reject

                intraday_pnl = 0.0
                for symbol, volume in lots.items():
                    if symbol not in open_prices or symbol not in close_prices:
                        continue
                    intraday_pnl += (
                        close_prices[symbol] - open_prices[symbol]
                    ) * int(volume) * PRODUCT_MULTIPLIERS[self._product(symbol)]
                equity += intraday_pnl
                high_watermark = max(high_watermark, equity)
                close_margin = self._margin(lots, close_prices) if lots else 0.0
                risk_reason = self.account_risk_reason(
                    equity=equity,
                    day_start_equity=day_start_equity,
                    high_watermark=high_watermark,
                    margin=close_margin,
                )
                if risk_reason:
                    first_divergence = first_divergence or risk_reason
                    if lots:
                        closing = {symbol: -volume for symbol, volume in lots.items()}
                        close_turnover = self._turnover(closing, close_prices)
                        turnover_notional += close_turnover
                        equity -= close_turnover * cost_rate
                        lots.clear()
                    if risk_reason == "daily loss limit reached":
                        daily_circuit = True
                    else:
                        halted = True

            valuation_prices = close_prices if close_prices else open_prices
            margin = self._margin(lots, valuation_prices) if lots else 0.0
            gross_notional = float(
                sum(
                    abs(int(volume))
                    * float(valuation_prices.get(symbol, 0.0))
                    * PRODUCT_MULTIPLIERS[self._product(symbol)]
                    for symbol, volume in lots.items()
                )
            )
            previous_close = {
                symbol: float(close_prices[symbol])
                for symbol in lots
                if symbol in close_prices
            }
            daily_return = equity / previous_equity - 1.0
            completed_returns.append(float(daily_return))
            completed_returns = completed_returns[-2:]
            output_rows.append(
                {
                    "date": day,
                    "equity": equity,
                    "daily_return": daily_return,
                    "turnover_notional": turnover_notional,
                    "gross_notional": gross_notional,
                    "margin": margin,
                    "risk_reason": risk_reason,
                    "margin_reject": margin_reject,
                    "daily_circuit": daily_circuit,
                    "risk_scale": risk_scale,
                    "halted": halted,
                }
            )

        daily = pd.DataFrame(output_rows)
        if daily.empty:
            daily = pd.DataFrame(
                columns=[
                    "equity", "daily_return", "turnover_notional",
                    "gross_notional", "margin", "risk_reason",
                    "margin_reject", "daily_circuit", "risk_scale",
                    "halted",
                ]
            )
        else:
            daily.set_index("date", inplace=True)
        return ProductionSimulationResult(
            daily=daily,
            final_equity=float(equity),
            first_divergence=first_divergence,
        )
