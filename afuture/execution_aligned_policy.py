"""Frozen execution-aligned directional portfolio policy.

The candidate pool was selected on the already-observed 2024-08-21..2026-08-20
specific-contract next-open history. Live template rotation remains causal: template
signals use the previous close and meta scores use only completed continuous-contract
close->open/open->close execution-proxy returns. Portfolio gross notional is capped at 2x.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .directional import (
    MAX_ABS_DAILY_RETURN,
    MAX_GROSS_LEVERAGE,
    _parse_template_id,
    _template_weight_path,
    _trailing_scores,
)

BASE_COST_BPS = 5.0

_EXECUTION_TEMPLATE_IDS = (
    "momentum_s20_f0_k1_r10_g2",
    "breakout_s120_f0_k1_r1_g2",
    "breakout_s40_f0_k1_r2_g2",
    "acceleration_s20_f5_k1_r10_g2",
    "moving_average_s60_f0_k1_r2_g2",
    "moving_average_s60_f0_k1_r5_g2",
    "acceleration_s20_f3_k1_r10_g2",
    "tsmom_s20_f0_k1_r10_g2",
    "breakout_s60_f0_k1_r2_g2",
    "breakout_s60_f0_k1_r1_g2",
    "moving_average_s60_f0_k1_r1_g2",
    "moving_average_s60_f0_k3_r2_g2",
    "tsmom_s40_f0_k3_r10_g2",
    "moving_average_s40_f0_k1_r10_g2",
    "breakout_s120_f0_k1_r2_g2",
    "tsmom_s40_f0_k2_r5_g2",
    "momentum_s20_f0_k3_r10_g2",
    "moving_average_s40_f0_k1_r2_g2",
    "acceleration_s40_f5_k3_r10_g2",
    "tsmom_s40_f0_k2_r10_g2",
    "breakout_s5_f0_k1_r5_g2",
    "breakout_s20_f0_k1_r2_g2",
    "acceleration_s40_f5_k2_r5_g2",
    "acceleration_s40_f5_k2_r10_g2",
    "moving_average_s60_f0_k2_r2_g2",
    "breakout_s40_f0_k1_r1_g2",
    "tsmom_s40_f0_k5_r10_g2",
    "tsmom_s40_f0_k2_r2_g2",
    "breakout_s40_f0_k2_r1_g2",
    "tsmom_s40_f0_k3_r5_g2",
    "acceleration_s40_f5_k2_r1_g2",
    "moving_average_s120_f0_k2_r5_g2",
    "breakout_s20_f0_k1_r5_g2",
    "moving_average_s120_f0_k1_r1_g2",
    "acceleration_s20_f5_k1_r5_g2",
    "tsmom_s40_f0_k5_r2_g2",
    "momentum_s20_f0_k1_r5_g2",
    "tsmom_s40_f0_k2_r1_g2",
    "moving_average_s40_f0_k1_r1_g2",
    "moving_average_s120_f0_k2_r10_g2",
    "acceleration_s40_f5_k5_r10_g2",
    "moving_average_s40_f0_k5_r2_g2",
    "momentum_s40_f0_k1_r2_g2",
    "momentum_s20_f0_k2_r10_g2",
    "acceleration_s40_f5_k1_r2_g2",
    "moving_average_s40_f0_k5_r5_g2",
    "breakout_s40_f0_k3_r1_g2",
    "tsmom_s20_f0_k5_r10_g2",
    "acceleration_s120_f10_k5_r1_g2",
    "moving_average_s60_f0_k3_r1_g2",
    "reversal_s0_f1_k1_r5_g2",
    "moving_average_s120_f0_k1_r5_g2",
    "moving_average_s40_f0_k5_r10_g2",
    "moving_average_s60_f0_k2_r1_g2",
    "tsmom_s10_f0_k2_r10_g2",
    "moving_average_s120_f0_k3_r2_g2",
    "reversal_s0_f1_k2_r5_g2",
    "breakout_s40_f0_k3_r2_g2",
    "breakout_s40_f0_k2_r2_g2",
    "acceleration_s20_f3_k3_r10_g2",
    "acceleration_s120_f10_k5_r2_g2",
    "tsmom_s40_f0_k1_r5_g2",
    "acceleration_s20_f3_k5_r10_g2",
    "moving_average_s120_f0_k5_r2_g2",
)
_EXECUTION_TEMPLATES = tuple(_parse_template_id(item) for item in _EXECUTION_TEMPLATE_IDS)


def _clean_prices(frame: pd.DataFrame, products: tuple[str, ...]) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(item).upper() for item in result.columns]
    requested = [str(item).upper() for item in products]
    missing = sorted(set(requested) - set(result.columns))
    if missing:
        raise ValueError(f"directional OHLC history missing products: {missing}")
    result = result[requested].astype(float).sort_index()
    return result.where(result > 0.0)


def _execution_proxy_stream(
    open_prices: pd.DataFrame,
    close: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    cost_bps: float = BASE_COST_BPS,
) -> pd.Series:
    gap = open_prices.div(close.shift(1)) - 1.0
    intraday = close.div(open_prices) - 1.0
    gap = gap.mask(gap.abs() > MAX_ABS_DAILY_RETURN).fillna(0.0)
    intraday = intraday.mask(intraday.abs() > MAX_ABS_DAILY_RETURN).fillna(0.0)
    old_weights = weights.shift(1).fillna(0.0)
    pnl = (old_weights * gap).sum(axis=1) + (weights * intraday).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = float(weights.iloc[0].abs().sum())
    return pnl - turnover * float(cost_bps) / 10000.0


@dataclass(frozen=True)
class ExecutionAlignedAggressivePolicy:
    products: tuple[str, ...]
    meta_lookback: int = 5
    meta_rebalance: int = 10
    meta_count: int = 4
    template_ids: tuple[str, ...] = _EXECUTION_TEMPLATE_IDS

    def __post_init__(self) -> None:
        if not self.products:
            raise ValueError("execution-aligned policy products cannot be empty")
        if self.template_ids != _EXECUTION_TEMPLATE_IDS:
            raise ValueError("execution-aligned template pool is frozen")

    def weight_history(
        self,
        open_prices: pd.DataFrame,
        close: pd.DataFrame,
    ) -> pd.DataFrame:
        close = _clean_prices(close, self.products)
        open_prices = _clean_prices(open_prices, self.products).reindex(close.index)
        returns = close.pct_change(fill_method=None)
        returns = returns.mask(returns.abs() > MAX_ABS_DAILY_RETURN)

        streams: dict[str, pd.Series] = {}
        paths: dict[str, pd.DataFrame] = {}
        for template_id, template in zip(self.template_ids, _EXECUTION_TEMPLATES):
            weights = _template_weight_path(returns, template)
            paths[template_id] = weights
            streams[template_id] = _execution_proxy_stream(
                open_prices,
                close,
                weights,
            )

        stream_frame = pd.DataFrame(streams).sort_index().fillna(0.0)
        scores = _trailing_scores(stream_frame, self.meta_lookback)
        names = list(stream_frame.columns)
        final = pd.DataFrame(0.0, index=close.index, columns=close.columns)
        selected: list[int] = []
        for position, timestamp in enumerate(close.index):
            if position >= self.meta_lookback and (
                not selected or position % self.meta_rebalance == 0
            ):
                row = scores[position]
                valid = np.flatnonzero(np.isfinite(row))
                selected = (
                    [
                        int(item)
                        for item in valid[np.argsort(-row[valid], kind="stable")][
                            : self.meta_count
                        ]
                    ]
                    if valid.size
                    else []
                )
            if selected:
                rows = [paths[names[item]].loc[timestamp] for item in selected]
                final.loc[timestamp] = pd.concat(rows, axis=1).mean(axis=1)

        gross = final.abs().sum(axis=1)
        if bool((gross > MAX_GROSS_LEVERAGE + 1e-10).any()):
            raise AssertionError("execution-aligned policy exceeded 2x gross")
        return final

    def target_weights(
        self,
        open_prices: pd.DataFrame,
        close: pd.DataFrame,
    ) -> dict[str, float]:
        history = self.weight_history(open_prices, close)
        if history.empty:
            return {}
        latest = history.iloc[-1]
        return {
            str(product): float(weight)
            for product, weight in latest.items()
            if abs(float(weight)) > 1e-15
        }
