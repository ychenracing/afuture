"""Causal low-gross-leverage return-target portfolio research.

This module is research-only. It forms same-exchange long/short commodity pairs from
lagged signals, charges turnover costs, and keeps gross exposure explicitly bounded.
Continuous contracts are discovery evidence only; any production promotion must later
pass specific-contract roll-safe validation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AlphaTemplate:
    family: str
    slow: int
    fast: int
    vol_window: int
    rebalance: int
    max_pairs: int
    gross_leverage: float


def primary_templates() -> tuple[AlphaTemplate, ...]:
    """Return a bounded, economically interpretable first-wave template set."""
    rows: list[AlphaTemplate] = []
    for slow in (10, 20, 40, 60, 120):
        for rebalance in (1, 5):
            rows.append(AlphaTemplate("momentum", slow, 0, 20, rebalance, 1, 1.0))
            rows.append(AlphaTemplate("momentum", slow, 0, 20, rebalance, 2, 1.5))
    for fast in (1, 3, 5, 10):
        for rebalance in (1, 2, 5):
            rows.append(AlphaTemplate("reversal", 0, fast, 20, rebalance, 1, 1.0))
    for slow, fast in ((20, 3), (40, 5), (60, 5), (60, 10), (120, 10)):
        for rebalance in (1, 5):
            rows.append(AlphaTemplate("slow_fast", slow, fast, 20, rebalance, 1, 1.0))
            rows.append(AlphaTemplate("slow_fast", slow, fast, 20, rebalance, 2, 1.5))
    for slow in (20, 40, 60):
        for rebalance in (1, 5):
            rows.append(AlphaTemplate("breakout", slow, 5, 20, rebalance, 1, 1.0))
            rows.append(AlphaTemplate("breakout", slow, 5, 20, rebalance, 2, 1.5))
    return tuple(rows)


def _cross_sectional_z(frame: pd.DataFrame) -> pd.DataFrame:
    mean = frame.mean(axis=1)
    std = frame.std(axis=1).replace(0.0, np.nan)
    return frame.sub(mean, axis=0).div(std, axis=0)


def _rolling_log_return(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    return np.log1p(returns.clip(lower=-0.99)).rolling(
        window, min_periods=window
    ).sum()


def signal_scores(returns: pd.DataFrame, template: AlphaTemplate) -> pd.DataFrame:
    """Build close-t scores; caller shifts execution to the following return."""
    if template.family == "momentum":
        return _rolling_log_return(returns, template.slow)
    if template.family == "reversal":
        return -_rolling_log_return(returns, template.fast)
    if template.family == "slow_fast":
        slow = _cross_sectional_z(_rolling_log_return(returns, template.slow))
        fast = _cross_sectional_z(_rolling_log_return(returns, template.fast))
        return slow - 0.5 * fast
    if template.family == "breakout":
        synthetic_price = (1.0 + returns.fillna(0.0)).cumprod()
        rolling_low = synthetic_price.rolling(
            template.slow, min_periods=template.slow
        ).min()
        rolling_high = synthetic_price.rolling(
            template.slow, min_periods=template.slow
        ).max()
        location = (synthetic_price - rolling_low) / (
            rolling_high - rolling_low
        ).replace(0.0, np.nan)
        acceleration = _rolling_log_return(returns, template.fast)
        return _cross_sectional_z(location) + 0.5 * _cross_sectional_z(acceleration)
    raise ValueError(f"unknown alpha family: {template.family}")


def _pair_weights(
    score: pd.Series,
    volatility: pd.Series,
    exchange_map: dict[str, str],
    *,
    max_pairs: int,
    gross_leverage: float,
) -> tuple[pd.Series, list[str]]:
    columns = list(score.index)
    weights = pd.Series(0.0, index=columns, dtype=float)
    available = pd.DataFrame(
        {"score": score, "volatility": volatility}, index=columns
    ).replace([np.inf, -np.inf], np.nan).dropna()
    available = available[available["volatility"] > 1e-12]
    if len(available) < 2 or max_pairs <= 0 or gross_leverage <= 0:
        return weights, []

    candidates: list[tuple[float, str, str]] = []
    for exchange in sorted(set(exchange_map.values())):
        names = [
            name
            for name in available.index
            if exchange_map.get(str(name)) == exchange
        ]
        if len(names) < 2:
            continue
        ranked = available.loc[names].sort_values(["score"], kind="stable")
        low_names = list(ranked.index)
        high_names = list(reversed(low_names))
        for long_name, short_name in zip(high_names, low_names):
            if long_name == short_name:
                continue
            spread = float(
                available.loc[long_name, "score"]
                - available.loc[short_name, "score"]
            )
            if not np.isfinite(spread) or spread <= 0:
                continue
            candidates.append((spread, str(long_name), str(short_name)))

    selected: list[tuple[float, str, str]] = []
    used: set[str] = set()
    for item in sorted(candidates, key=lambda row: (-row[0], row[1], row[2])):
        _, long_name, short_name = item
        if long_name in used or short_name in used:
            continue
        selected.append(item)
        used.add(long_name)
        used.add(short_name)
        if len(selected) >= max_pairs:
            break
    if not selected:
        return weights, []

    pair_gross = gross_leverage / len(selected)
    legs: list[str] = []
    for _, long_name, short_name in selected:
        long_inv = 1.0 / float(available.loc[long_name, "volatility"])
        short_inv = 1.0 / float(available.loc[short_name, "volatility"])
        scale = pair_gross / (long_inv + short_inv)
        weights.loc[long_name] = long_inv * scale
        weights.loc[short_name] = -short_inv * scale
        legs.extend((long_name, short_name))

    gross = float(weights.abs().sum())
    if gross > gross_leverage + 1e-12:
        weights *= gross_leverage / gross
    return weights, legs


def simulate_template(
    returns: pd.DataFrame,
    exchange_map: dict[str, str],
    template: AlphaTemplate,
    *,
    cost_bps: float,
) -> tuple[pd.Series, list[dict]]:
    """Simulate one causal template with decisions made one row before PnL."""
    data = returns.astype(float).replace([np.inf, -np.inf], np.nan)
    scores = signal_scores(data, template)
    volatility = data.rolling(
        template.vol_window, min_periods=template.vol_window
    ).std()
    previous = pd.Series(0.0, index=data.columns, dtype=float)
    output = pd.Series(0.0, index=data.index, dtype=float)
    audit: list[dict] = []

    for index in range(1, len(data)):
        legs: list[str] = [str(name) for name in previous[previous != 0].index]
        if index % template.rebalance == 0:
            next_weights, legs = _pair_weights(
                scores.iloc[index - 1],
                volatility.iloc[index - 1],
                exchange_map,
                max_pairs=template.max_pairs,
                gross_leverage=template.gross_leverage,
            )
            turnover = float((next_weights - previous).abs().sum())
            output.iloc[index] -= turnover * cost_bps / 10000.0
            previous = next_weights
        realized = data.iloc[index].fillna(0.0)
        output.iloc[index] += float((previous * realized).sum())
        audit.append(
            {
                "date": data.index[index],
                "gross": float(previous.abs().sum()),
                "legs": legs,
            }
        )
    return output, audit


def _metrics(series: pd.Series) -> dict:
    values = series.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if values.empty:
        return {
            "days": 0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }
    equity = (1.0 + values).cumprod()
    total = float(equity.iloc[-1] - 1.0)
    annualized = (
        (1.0 + total) ** (252.0 / len(values)) - 1.0
        if total > -1.0
        else -1.0
    )
    std = float(values.std(ddof=1))
    sharpe = float(values.mean() / std * np.sqrt(252.0)) if std > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    return {
        "days": int(len(values)),
        "annualized_return": annualized,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
    }


def choose_templates(
    streams: dict[str, pd.Series],
    *,
    start,
    end,
    count: int,
) -> list[str]:
    """Select positive calibration streams only; evaluation rows are never inspected."""
    if count <= 0:
        return []
    ranked: list[tuple[float, str]] = []
    for name, series in streams.items():
        calibration = series.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        item = _metrics(calibration)
        if item["annualized_return"] <= 0.0:
            continue
        score = (
            4.0 * item["annualized_return"]
            + 0.5 * item["sharpe"]
            - 2.0 * abs(item["max_drawdown"])
        )
        ranked.append((score, name))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in ranked[:count]]


def main() -> None:
    raise SystemExit(
        "real-data evaluation is added in the next milestone; core simulator only"
    )


if __name__ == "__main__":
    main()
