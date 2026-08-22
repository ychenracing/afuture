"""Frozen aggressive directional portfolio primitives.

This module contains the production copy of the execution-aware 2026-08-22 policy.
Research scripts may evolve independently; live behavior is frozen here so later research
edits cannot silently change production signals. The policy is causal, account-exclusive,
and capped at 2x gross target notional before the existing account/margin/microstructure
risk gates apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor
import re
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .models import (
    AccountSnapshot,
    ContractInfo,
    ContractPosition,
    ContractSpec,
    Tick,
)

MAX_GROSS_LEVERAGE = 2.0
MAX_ABS_DAILY_RETURN = 0.20

_FROZEN_TEMPLATE_IDS = (
    "breakout_s10_f0_k1_r2_g2",
    "breakout_s120_f0_k1_r1_g2",
    "moving_average_s60_f0_k1_r5_g2",
    "breakout_s40_f0_k2_r1_g2",
    "moving_average_s60_f0_k1_r1_g2",
    "breakout_s5_f0_k1_r2_g2",
    "reversal_s0_f1_k5_r10_g2",
    "breakout_s60_f0_k1_r1_g2",
    "tsmom_s40_f0_k2_r5_g2",
    "reversal_s0_f5_k1_r2_g2",
    "breakout_s40_f0_k1_r1_g2",
    "reversal_s0_f10_k1_r2_g2",
    "reversal_s0_f1_k3_r10_g2",
    "reversal_s0_f5_k1_r1_g2",
    "reversal_s0_f5_k5_r10_g2",
    "reversal_s0_f5_k2_r2_g2",
    "reversal_s0_f5_k2_r10_g2",
    "reversal_s0_f1_k1_r5_g2",
    "breakout_s20_f0_k1_r2_g2",
    "breakout_s40_f0_k3_r1_g2",
    "moving_average_s60_f0_k1_r2_g2",
    "breakout_s40_f0_k5_r10_g2",
    "reversal_s0_f1_k2_r10_g2",
    "breakout_s20_f0_k1_r5_g2",
    "breakout_s60_f0_k2_r1_g2",
    "momentum_s20_f0_k1_r5_g2",
    "moving_average_s120_f0_k1_r10_g2",
    "reversal_s0_f1_k2_r5_g2",
    "acceleration_s10_f3_k1_r10_g2",
    "moving_average_s120_f0_k2_r5_g2",
    "reversal_s0_f3_k2_r10_g2",
    "breakout_s10_f0_k1_r5_g2",
)


@dataclass(frozen=True)
class DirectionalConfig:
    enabled: bool = False
    products: tuple[str, ...] = ()
    exchanges: tuple[str, ...] = ("DCE", "CZCE", "SHFE", "INE")
    max_gross_leverage: float = MAX_GROSS_LEVERAGE
    min_days_to_expiry: int = 20
    min_volume: float = 1000.0
    min_open_interest: float = 5000.0
    max_contract_volume: int = 100
    rebalance_window: str = "21:00-21:10"
    signal_max_age_hours: float = 36.0
    account_exclusive: bool = True

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.products:
            raise ValueError("directional products cannot be empty")
        if not self.exchanges:
            raise ValueError("directional exchanges cannot be empty")
        if not 0 < self.max_gross_leverage <= MAX_GROSS_LEVERAGE:
            raise ValueError("directional gross leverage must be in (0, 2.0]")
        if self.min_days_to_expiry < 0:
            raise ValueError("directional min_days_to_expiry cannot be negative")
        if self.min_volume < 0 or self.min_open_interest < 0:
            raise ValueError("directional activity thresholds cannot be negative")
        if self.max_contract_volume <= 0:
            raise ValueError("directional max_contract_volume must be positive")
        if self.signal_max_age_hours <= 0:
            raise ValueError("directional signal_max_age_hours must be positive")
        if not self.account_exclusive:
            raise ValueError("directional production mode must be account-exclusive")
        _parse_window(self.rebalance_window)


@dataclass(frozen=True)
class _Template:
    family: str
    slow: int
    fast: int
    max_products: int
    rebalance: int
    gross_leverage: float = MAX_GROSS_LEVERAGE


def _parse_template_id(raw: str) -> _Template:
    match = re.fullmatch(
        r"(.+)_s(\d+)_f(\d+)_k(\d+)_r(\d+)_g([0-9.]+)", raw
    )
    if match is None:
        raise ValueError(f"invalid frozen directional template: {raw}")
    family, slow, fast, max_products, rebalance, gross = match.groups()
    value = float(gross)
    if value > MAX_GROSS_LEVERAGE:
        raise ValueError(f"frozen template exceeds gross cap: {raw}")
    return _Template(
        family,
        int(slow),
        int(fast),
        int(max_products),
        int(rebalance),
        value,
    )


_FROZEN_TEMPLATES = tuple(_parse_template_id(item) for item in _FROZEN_TEMPLATE_IDS)


def _rolling_log_return(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    return np.log1p(returns.clip(lower=-0.99)).rolling(
        window, min_periods=window
    ).sum()


def _normalized_price(returns: pd.DataFrame) -> pd.DataFrame:
    return (1.0 + returns.fillna(0.0)).cumprod()


def _signal_scores(returns: pd.DataFrame, template: _Template) -> pd.DataFrame:
    vol20 = returns.rolling(20, min_periods=20).std().replace(0.0, np.nan)
    if template.family == "tsmom":
        momentum = _rolling_log_return(returns, template.slow)
        return momentum / (vol20 * np.sqrt(max(template.slow, 1)))
    if template.family == "momentum":
        return _rolling_log_return(returns, template.slow)
    if template.family == "reversal":
        momentum = _rolling_log_return(returns, template.fast)
        return -momentum / (vol20 * np.sqrt(max(template.fast, 1)))
    if template.family == "moving_average":
        price = _normalized_price(returns)
        average = price.rolling(
            template.slow, min_periods=template.slow
        ).mean()
        deviation = price / average - 1.0
        return deviation / (vol20 * np.sqrt(max(template.slow, 1)))
    if template.family == "breakout":
        price = _normalized_price(returns)
        rolling_low = price.rolling(
            template.slow, min_periods=template.slow
        ).min()
        rolling_high = price.rolling(
            template.slow, min_periods=template.slow
        ).max()
        return (
            (price - rolling_low)
            / (rolling_high - rolling_low).replace(0.0, np.nan)
            - 0.5
        )
    if template.family == "acceleration":
        slow = _rolling_log_return(returns, template.slow)
        fast = _rolling_log_return(returns, template.fast)
        return (slow - fast) / (vol20 * np.sqrt(max(template.slow, 1)))
    raise ValueError(f"unknown frozen directional family: {template.family}")


def _template_weight_path(
    returns: pd.DataFrame, template: _Template
) -> pd.DataFrame:
    scores = _signal_scores(returns, template).to_numpy(float)
    current = np.zeros(returns.shape[1], dtype=float)
    audit = np.zeros(returns.shape, dtype=float)
    step = max(template.rebalance, 1)
    for position in range(1, len(returns)):
        if position % step == 0:
            lagged = scores[position - 1]
            valid = np.flatnonzero(
                np.isfinite(lagged) & (np.abs(lagged) > 1e-12)
            )
            next_weights = np.zeros(returns.shape[1], dtype=float)
            if valid.size:
                order = valid[
                    np.argsort(-np.abs(lagged[valid]), kind="stable")
                ]
                selected = order[: min(template.max_products, len(order))]
                if selected.size:
                    each = min(
                        template.gross_leverage, MAX_GROSS_LEVERAGE
                    ) / float(selected.size)
                    next_weights[selected] = np.sign(lagged[selected]) * each
            current = next_weights
        audit[position] = current
    return pd.DataFrame(audit, index=returns.index, columns=returns.columns)


def _theoretical_stream(
    returns: pd.DataFrame, weights: pd.DataFrame, cost_bps: float = 5.0
) -> pd.Series:
    realized = returns.fillna(0.0)
    gross = (weights * realized).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = float(weights.iloc[0].abs().sum())
    return gross - turnover * cost_bps / 10000.0


def _trailing_scores(frame: pd.DataFrame, lookback: int = 10) -> np.ndarray:
    values = frame.fillna(0.0).to_numpy(float)
    score = np.full_like(values, np.nan, dtype=float)
    for index in range(lookback, len(frame)):
        history = values[index - lookback : index]
        log_total = np.log1p(np.clip(history, -0.999999, None)).sum(axis=0)
        annualized = np.exp(log_total * (252.0 / lookback)) - 1.0
        mean = history.mean(axis=0)
        std = history.std(axis=0, ddof=1)
        sharpe = np.zeros(history.shape[1], dtype=float)
        valid = std > 1e-12
        sharpe[valid] = mean[valid] / std[valid] * np.sqrt(252.0)
        row = 4.0 * annualized + 0.5 * sharpe
        row[annualized <= 0.0] = np.nan
        score[index] = row
    return score


@dataclass(frozen=True)
class FrozenAggressivePolicy:
    products: tuple[str, ...]
    meta_lookback: int = 10
    meta_rebalance: int = 5
    meta_count: int = 2

    def __post_init__(self) -> None:
        if not self.products:
            raise ValueError("frozen directional policy products cannot be empty")

    def weight_history(self, close: pd.DataFrame) -> pd.DataFrame:
        frame = close.copy()
        frame.columns = [str(item).upper() for item in frame.columns]
        requested = [str(item).upper() for item in self.products]
        missing = sorted(set(requested) - set(frame.columns))
        if missing:
            raise ValueError(f"directional close history missing products: {missing}")
        frame = frame[requested].astype(float).sort_index()
        returns = frame.pct_change(fill_method=None)
        returns = returns.mask(returns.abs() > MAX_ABS_DAILY_RETURN)

        streams: dict[str, pd.Series] = {}
        paths: dict[str, pd.DataFrame] = {}
        for template_id, template in zip(
            _FROZEN_TEMPLATE_IDS, _FROZEN_TEMPLATES
        ):
            weights = _template_weight_path(returns, template)
            paths[template_id] = weights
            streams[template_id] = _theoretical_stream(returns, weights)

        stream_frame = pd.DataFrame(streams).sort_index().fillna(0.0)
        scores = _trailing_scores(stream_frame, self.meta_lookback)
        names = list(stream_frame.columns)
        final = pd.DataFrame(0.0, index=frame.index, columns=frame.columns)
        selected: list[int] = []
        for position, timestamp in enumerate(frame.index):
            if position >= self.meta_lookback and (
                not selected or position % self.meta_rebalance == 0
            ):
                row = scores[position]
                valid = np.flatnonzero(np.isfinite(row))
                selected = (
                    [
                        int(item)
                        for item in valid[
                            np.argsort(-row[valid], kind="stable")
                        ][: self.meta_count]
                    ]
                    if valid.size
                    else []
                )
            if selected:
                rows = [paths[names[item]].loc[timestamp] for item in selected]
                final.loc[timestamp] = pd.concat(rows, axis=1).mean(axis=1)
        gross = final.abs().sum(axis=1)
        if bool((gross > MAX_GROSS_LEVERAGE + 1e-10).any()):
            raise AssertionError("frozen directional policy exceeded 2x gross")
        return final

    def target_weights(self, close: pd.DataFrame) -> dict[str, float]:
        history = self.weight_history(close)
        if history.empty:
            return {}
        latest = history.iloc[-1]
        return {
            str(product): float(weight)
            for product, weight in latest.items()
            if abs(float(weight)) > 1e-15
        }


class DirectionalContractSelector:
    def __init__(self, config: DirectionalConfig) -> None:
        config.validate()
        self.config = config

    def select(
        self,
        catalog: Iterable[ContractInfo],
        ticks: Mapping[str, Tick],
        today: date,
    ) -> dict[str, ContractInfo]:
        products = {item.upper() for item in self.config.products}
        exchanges = {item.upper() for item in self.config.exchanges}
        candidates: dict[str, list[tuple[float, float, date, ContractInfo]]] = {}
        for item in catalog:
            product = item.product.upper()
            if product not in products or item.exchange.upper() not in exchanges:
                continue
            if item.listing:
                try:
                    if date.fromisoformat(item.listing) > today:
                        continue
                except ValueError:
                    continue
            try:
                expiry = date.fromisoformat(item.expiry)
            except ValueError:
                continue
            if (expiry - today).days < self.config.min_days_to_expiry:
                continue
            tick = ticks.get(item.symbol)
            if tick is None:
                continue
            if tick.volume < self.config.min_volume:
                continue
            if tick.open_interest < self.config.min_open_interest:
                continue
            candidates.setdefault(product, []).append(
                (tick.open_interest, tick.volume, expiry, item)
            )

        result: dict[str, ContractInfo] = {}
        for product, rows in candidates.items():
            rows.sort(
                key=lambda row: (-row[0], -row[1], row[2], row[3].symbol)
            )
            result[product] = rows[0][3]
        return result


@dataclass(frozen=True)
class RebalancePlan:
    reductions: dict[str, int] = field(default_factory=dict)
    openings: dict[str, int] = field(default_factory=dict)


def build_target_lots(
    account: AccountSnapshot,
    product_weights: Mapping[str, float],
    product_ticks: Mapping[str, Tick],
    specs: Mapping[str, ContractSpec],
    *,
    max_contract_volume: int,
) -> dict[str, int]:
    targets: dict[str, int] = {}
    gross = sum(abs(float(value)) for value in product_weights.values())
    if gross > MAX_GROSS_LEVERAGE + 1e-10:
        raise ValueError(f"target product weights exceed 2x gross: {gross}")
    for product, raw_weight in product_weights.items():
        weight = float(raw_weight)
        if abs(weight) <= 1e-15:
            continue
        tick = product_ticks.get(product)
        if tick is None:
            continue
        spec = specs.get(tick.symbol)
        if spec is None:
            continue
        price = tick.mid_price
        notional = price * spec.multiplier
        if price <= 0 or notional <= 0:
            continue
        lots = min(
            max_contract_volume,
            floor(account.equity * abs(weight) / notional),
        )
        if lots > 0:
            targets[tick.symbol] = lots if weight > 0 else -lots
    return targets


def build_rebalance_plan(
    positions: Iterable[ContractPosition], target_lots: Mapping[str, int]
) -> RebalancePlan:
    current = {
        position.symbol: position.net_volume
        for position in positions
        if position.net_volume != 0
    }
    symbols = set(current) | set(target_lots)
    reductions: dict[str, int] = {}
    potential_openings: dict[str, int] = {}

    for symbol in sorted(symbols):
        have = int(current.get(symbol, 0))
        target = int(target_lots.get(symbol, 0))
        if have == target:
            continue
        if have == 0:
            if target:
                potential_openings[symbol] = target
            continue
        if target == 0:
            reductions[symbol] = -have
            continue
        if (have > 0) != (target > 0):
            reductions[symbol] = -have
            continue
        if abs(target) < abs(have):
            reductions[symbol] = target - have
        elif abs(target) > abs(have):
            potential_openings[symbol] = target - have

    return RebalancePlan(
        reductions=reductions,
        openings={} if reductions else potential_openings,
    )


def _parse_window(raw: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{2}:\d{2})-(\d{2}:\d{2})", str(raw))
    if match is None or match.group(1) == match.group(2):
        raise ValueError(f"invalid directional rebalance window: {raw}")
    return match.group(1), match.group(2)
