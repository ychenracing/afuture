"""Causal broad-universe screen for market-neutral futures relative-value families.

The screen uses Sina continuous daily contracts only to answer whether broader
cross-sectional information contains robust incremental alpha. Continuous-series
results are never sufficient for live promotion: survivors must pass specific-contract
and Shadow execution gates before they can place production orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

WINDOWS = {
    "prior1": ("2022-08-22", "2023-08-20"),
    "prior2": ("2023-08-21", "2024-08-20"),
    "prior_full": ("2022-08-22", "2024-08-20"),
    "train": ("2024-08-21", "2025-08-20"),
    "validation": ("2025-08-21", "2026-02-20"),
    "selection_full": ("2024-08-21", "2026-02-20"),
    "oos": ("2026-02-21", "2026-08-20"),
    "full_recent": ("2024-08-21", "2026-08-20"),
}

BASE_COST_BPS = 15.0
STRESS_COST_BPS = 30.0
MAX_ABS_DAILY_RETURN = 0.20
LEVERAGE_GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)


@dataclass(frozen=True)
class Candidate:
    family: str
    slow: int
    fast: int
    rebalance: int
    tail_fraction: float


# Small preregistered family set. Do not expand after seeing OOS.
CANDIDATES = (
    Candidate("momentum", 20, 0, 5, 0.25),
    Candidate("momentum", 20, 0, 20, 0.25),
    Candidate("momentum", 60, 0, 5, 0.25),
    Candidate("momentum", 60, 0, 20, 0.25),
    Candidate("momentum", 120, 0, 5, 0.25),
    Candidate("momentum", 120, 0, 20, 0.25),
    Candidate("slow_fast", 60, 5, 5, 0.25),
    Candidate("slow_fast", 60, 5, 20, 0.25),
    Candidate("slow_fast", 120, 5, 5, 0.25),
    Candidate("slow_fast", 120, 5, 20, 0.25),
    Candidate("slow_fast", 120, 10, 5, 0.25),
    Candidate("slow_fast", 120, 10, 20, 0.25),
    Candidate("reversal", 0, 5, 5, 0.25),
    Candidate("reversal", 0, 10, 5, 0.25),
    Candidate("negative_skew", 60, 0, 20, 0.25),
    Candidate("negative_skew", 120, 0, 20, 0.25),
)


def _rolling_sum(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return np.log1p(values.clip(lower=-0.99)).rolling(
        window, min_periods=window
    ).sum()


def _cross_sectional_z(frame: pd.DataFrame) -> pd.DataFrame:
    mean = frame.mean(axis=1)
    std = frame.std(axis=1).replace(0.0, np.nan)
    return frame.sub(mean, axis=0).div(std, axis=0)


def build_panel(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    data = raw.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data.dropna(subset=["date", "product", "close"], inplace=True)
    data.drop_duplicates(["date", "product"], keep="last", inplace=True)
    close = (
        data.pivot(index="date", columns="product", values="close")
        .sort_index()
        .astype(float)
    )
    returns = close.pct_change(fill_method=None)
    outlier_mask = returns.abs() > MAX_ABS_DAILY_RETURN
    outlier_count = int(outlier_mask.sum().sum())
    returns = returns.mask(outlier_mask)
    coverage = {
        "products": int(close.shape[1]),
        "trading_days": int(close.shape[0]),
        "return_outliers_removed": outlier_count,
        "first": close.index.min().date().isoformat(),
        "last": close.index.max().date().isoformat(),
    }
    return returns, coverage


def signal_for(returns: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    if candidate.family == "momentum":
        return _rolling_sum(returns, candidate.slow)
    if candidate.family == "slow_fast":
        slow = _cross_sectional_z(_rolling_sum(returns, candidate.slow))
        fast = _cross_sectional_z(_rolling_sum(returns, candidate.fast))
        return slow - 0.5 * fast
    if candidate.family == "reversal":
        return -_rolling_sum(returns, candidate.fast)
    if candidate.family == "negative_skew":
        return -returns.rolling(
            candidate.slow, min_periods=candidate.slow
        ).skew()
    raise ValueError(f"unknown family: {candidate.family}")


def simulate(
    returns: pd.DataFrame,
    signal: pd.DataFrame,
    *,
    rebalance: int,
    tail_fraction: float,
    cost_bps: float,
) -> pd.Series:
    columns = list(returns.columns)
    previous = pd.Series(0.0, index=columns)
    output = pd.Series(0.0, index=returns.index)
    volatility = returns.rolling(20, min_periods=20).std()

    for index in range(1, len(returns)):
        cost = 0.0
        if index % rebalance == 0:
            available = pd.DataFrame(
                {
                    "signal": signal.iloc[index - 1],
                    "volatility": volatility.iloc[index - 1],
                }
            ).replace([np.inf, -np.inf], np.nan).dropna()
            available = available[available["volatility"] > 1e-8]
            next_weights = pd.Series(0.0, index=columns)
            tail = max(1, int(len(available) * tail_fraction))
            if len(available) >= tail * 2:
                short_names = available.nsmallest(tail, "signal").index
                long_names = available.nlargest(tail, "signal").index
                long_inverse_vol = 1.0 / available.loc[long_names, "volatility"]
                short_inverse_vol = 1.0 / available.loc[short_names, "volatility"]
                next_weights.loc[long_names] = (
                    long_inverse_vol / long_inverse_vol.sum() * 0.5
                )
                next_weights.loc[short_names] = -(
                    short_inverse_vol / short_inverse_vol.sum() * 0.5
                )
            turnover = float((next_weights - previous).abs().sum())
            cost = turnover * cost_bps / 10000.0
            previous = next_weights

        realized = returns.iloc[index].fillna(0.0)
        output.iloc[index] = float((previous * realized).sum()) - cost

    return output


def metrics(series: pd.Series) -> dict:
    values = series.replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return {
            "days": 0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "annualized_volatility": 0.0,
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
    std = values.std(ddof=1)
    volatility = float(std * np.sqrt(252.0))
    sharpe = float(values.mean() / std * np.sqrt(252.0)) if std > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    return {
        "days": int(len(values)),
        "total_return": total,
        "annualized_return": float(annualized),
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
    }


def window_metrics(series: pd.Series, window: str) -> dict:
    start, end = WINDOWS[window]
    return metrics(series.loc[pd.Timestamp(start):pd.Timestamp(end)])


def candidate_passes(row: dict, left: str, right: str) -> bool:
    for window in (left, right):
        item = row["base"][window]
        if item["annualized_return"] <= 0 or item["sharpe"] <= 0.20:
            return False
        if item["max_drawdown"] <= -0.15:
            return False
    stress_key = "prior_full" if left.startswith("prior") else "selection_full"
    stress = row["stress"][stress_key]
    return stress["annualized_return"] > 0 and stress["max_drawdown"] > -0.18


def evaluate_candidates(returns: pd.DataFrame) -> list[dict]:
    results: list[dict] = []
    for candidate in CANDIDATES:
        signal = signal_for(returns, candidate)
        base_series = simulate(
            returns,
            signal,
            rebalance=candidate.rebalance,
            tail_fraction=candidate.tail_fraction,
            cost_bps=BASE_COST_BPS,
        )
        stress_series = simulate(
            returns,
            signal,
            rebalance=candidate.rebalance,
            tail_fraction=candidate.tail_fraction,
            cost_bps=STRESS_COST_BPS,
        )
        row = {
            "candidate": asdict(candidate),
            "base": {name: window_metrics(base_series, name) for name in WINDOWS},
            "stress": {name: window_metrics(stress_series, name) for name in WINDOWS},
        }
        row["prior_pass"] = candidate_passes(row, "prior1", "prior2")
        row["current_pass"] = candidate_passes(row, "train", "validation")
        results.append(row)
    return results


def _selection_score(row: dict, left: str, right: str) -> float:
    return min(row["base"][left]["sharpe"], row["base"][right]["sharpe"])


def select_candidate(results: list[dict], phase: str) -> dict | None:
    if phase == "prior":
        rows = [row for row in results if row["prior_pass"]]
        left, right = "prior1", "prior2"
    elif phase == "current":
        rows = [row for row in results if row["current_pass"]]
        left, right = "train", "validation"
    else:
        raise ValueError("phase must be prior or current")
    if not rows:
        return None
    return max(rows, key=lambda row: _selection_score(row, left, right))


def family_support(results: list[dict], phase: str) -> dict[str, int]:
    key = "prior_pass" if phase == "prior" else "current_pass"
    support: dict[str, int] = {}
    for row in results:
        if row[key]:
            family = row["candidate"]["family"]
            support[family] = support.get(family, 0) + 1
    return support


def choose_leverage(series: pd.Series) -> float:
    selected = 1.0
    for leverage in LEVERAGE_GRID:
        scaled = series * leverage
        item = metrics(scaled)
        if (
            item["annualized_return"] > 0
            and item["max_drawdown"] > -0.15
            and (scaled > -1.0).all()
        ):
            selected = leverage
    return selected


def series_for_candidate(
    returns: pd.DataFrame, row: dict, cost_bps: float
) -> pd.Series:
    candidate = Candidate(**row["candidate"])
    return simulate(
        returns,
        signal_for(returns, candidate),
        rebalance=candidate.rebalance,
        tail_fraction=candidate.tail_fraction,
        cost_bps=cost_bps,
    )


def evaluate(raw: pd.DataFrame) -> dict:
    returns, coverage = build_panel(raw)
    results = evaluate_candidates(returns)
    prior = select_candidate(results, "prior")
    current = select_candidate(results, "current")
    prior_support = family_support(results, "prior")
    current_support = family_support(results, "current")

    report = {
        "source": "AKShare/Sina continuous daily futures bars",
        "role": "L3 breadth/factor screen only; not production execution evidence",
        "historical_l1_available": False,
        "continuous_roll_series": True,
        "pristine_final_oos": False,
        "cost_bps_one_way": BASE_COST_BPS,
        "stress_cost_bps_one_way": STRESS_COST_BPS,
        "coverage": coverage,
        "candidate_count": len(results),
        "prior_family_support": prior_support,
        "current_family_support": current_support,
        "candidates": results,
        "prior_selected": prior["candidate"] if prior else None,
        "current_selected": current["candidate"] if current else None,
    }

    if prior is not None:
        prior_series = series_for_candidate(returns, prior, STRESS_COST_BPS)
        report["prior_forward_train"] = window_metrics(prior_series, "train")
    else:
        report["prior_forward_train"] = None

    if current is not None:
        stressed_series = series_for_candidate(returns, current, STRESS_COST_BPS)
        selection_start, selection_end = WINDOWS["selection_full"]
        calibration = stressed_series.loc[
            pd.Timestamp(selection_start):pd.Timestamp(selection_end)
        ]
        leverage = choose_leverage(calibration)
        base_series = series_for_candidate(returns, current, BASE_COST_BPS) * leverage
        stress_scaled = stressed_series * leverage
        report["selected_leverage"] = leverage
        report["selected_base"] = {
            name: window_metrics(base_series, name)
            for name in ("train", "validation", "oos", "full_recent")
        }
        report["selected_stress"] = {
            name: window_metrics(stress_scaled, name)
            for name in ("train", "validation", "oos", "full_recent")
        }
    else:
        report["selected_leverage"] = 1.0
        report["selected_base"] = None
        report["selected_stress"] = None

    reasons: list[str] = []
    if prior is None:
        reasons.append("no family survives both independent prior subwindows")
    elif report["prior_forward_train"]["annualized_return"] <= 0:
        reasons.append("prior-selected family fails forward train")
    if current is None:
        reasons.append("no family survives train and validation")
    else:
        support = current_support.get(current["candidate"]["family"], 0)
        if support < 2:
            reasons.append("selected family has fewer than two stable configurations")
        full_recent = report["selected_stress"]["full_recent"]
        oos = report["selected_stress"]["oos"]
        if full_recent["annualized_return"] < 1.0:
            reasons.append("stressed two-year annualized return is below 100%")
        if full_recent["max_drawdown"] <= -0.20:
            reasons.append("stressed two-year drawdown exceeds 20%")
        if oos["annualized_return"] <= 0:
            reasons.append("final OOS return is not positive")

    report["target"] = {
        "annualized_return": 1.0,
        "max_drawdown": -0.20,
        "target_met": not reasons,
        "reasons": reasons,
    }
    return report


def main() -> None:
    path = Path("runtime/broad_daily_universe.csv")
    if not path.exists():
        raise SystemExit("broad daily universe missing; run fetch_broad_daily_universe.py")
    report = evaluate(pd.read_csv(path))
    output = Path("runtime/broad_relative_family_report.json")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "coverage": report["coverage"],
        "prior_selected": report["prior_selected"],
        "current_selected": report["current_selected"],
        "prior_forward_train": report["prior_forward_train"],
        "selected_leverage": report["selected_leverage"],
        "selected_stress": report["selected_stress"],
        "target": report["target"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
