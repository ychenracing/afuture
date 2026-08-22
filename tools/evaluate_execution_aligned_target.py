"""Final execution-aligned roll-safe L4 for the aggressive directional portfolio.

The 64-template pool was fitted on already-observed specific-contract next-open history.
Daily live rotation itself remains causal and production-reproducible: signals use the
previous close and meta scores use only completed continuous-contract close->open plus
open->close execution-proxy returns. Gross exposure is capped at 2x.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_aggressive_directional as aggressive
import evaluate_return_target_specific as specific

MAX_GROSS_LEVERAGE = 2.0
BASE_COST_BPS = 5.0
STRESS_COST_BPS = 15.0
EXTREME_COST_BPS = 30.0
PRISTINE_FINAL_OOS = False
REQUIRED_PRODUCTS = specific.REQUIRED_PRODUCTS
WINDOWS = aggressive.WINDOWS

EXECUTION_SELECTION = {
    "selection_bias": "full_recent_target_fit_plus_specific_execution_pool_fit",
    "pool_size": 64,
    "pool_ids": [
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
    ],
    "meta_lookback": 5,
    "rebalance": 10,
    "count": 4,
    "effective_gross_leverage": 2.0,
    "meta_score_source": "continuous_next_open_proxy",
    "execution_timing": "prior_close_signal_next_session_open_rebalance",
}


def build_continuous_execution_proxy(
    raw: pd.DataFrame,
    *,
    products: tuple[str, ...] = REQUIRED_PRODUCTS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["product"] = frame["product"].astype(str).str.upper()
    for column in ("open", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "product", "open", "close"])
    frame = frame[(frame["open"] > 0) & (frame["close"] > 0)]
    frame.drop_duplicates(["date", "product"], keep="last", inplace=True)
    open_prices = (
        frame.pivot(index="date", columns="product", values="open")
        .sort_index()
        .reindex(columns=list(products))
    )
    close = (
        frame.pivot(index="date", columns="product", values="close")
        .sort_index()
        .reindex(index=open_prices.index, columns=open_prices.columns)
    )
    gap = open_prices.div(close.shift(1)) - 1.0
    intraday = close.div(open_prices) - 1.0
    gap = gap.mask(gap.abs() > aggressive.MAX_ABS_DAILY_RETURN)
    intraday = intraday.mask(intraday.abs() > aggressive.MAX_ABS_DAILY_RETURN)
    return gap, intraday


def _template_lookup() -> dict[str, aggressive.DirectionalTemplate]:
    lookup = {
        aggressive.template_id(template): template
        for template in aggressive.directional_templates()
    }
    missing = [item for item in EXECUTION_SELECTION["pool_ids"] if item not in lookup]
    if missing:
        raise ValueError(f"execution-aligned templates missing: {missing}")
    return lookup


def _meta_weight_path(
    score_streams: dict[str, pd.Series],
    paths: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    frame = pd.DataFrame(score_streams).sort_index().fillna(0.0)
    names = list(frame.columns)
    scores = aggressive._trailing_score_matrix(
        frame, int(EXECUTION_SELECTION["meta_lookback"])
    )
    example = paths[names[0]].reindex(frame.index).fillna(0.0)
    final = pd.DataFrame(0.0, index=frame.index, columns=example.columns)
    selected: list[int] = []
    lookback = int(EXECUTION_SELECTION["meta_lookback"])
    rebalance = int(EXECUTION_SELECTION["rebalance"])
    count = int(EXECUTION_SELECTION["count"])
    for position, timestamp in enumerate(frame.index):
        if position >= lookback and (not selected or position % rebalance == 0):
            row = scores[position]
            valid = np.flatnonzero(np.isfinite(row))
            selected = (
                [int(item) for item in valid[np.argsort(-row[valid], kind="stable")][:count]]
                if valid.size
                else []
            )
        if selected:
            rows = [paths[names[item]].loc[timestamp] for item in selected]
            final.loc[timestamp] = pd.concat(rows, axis=1).mean(axis=1)
    gross = final.abs().sum(axis=1)
    if bool((gross > MAX_GROSS_LEVERAGE + 1e-10).any()):
        raise AssertionError("execution-aligned meta path exceeds 2x gross")
    return final


def generate_execution_signal_weights(continuous_raw: pd.DataFrame) -> pd.DataFrame:
    signal_returns, _ = aggressive.build_panel(continuous_raw)
    signal_returns.columns = [str(column).upper() for column in signal_returns.columns]
    missing = sorted(set(REQUIRED_PRODUCTS) - set(signal_returns.columns))
    if missing:
        raise ValueError(f"continuous signal feed missing products: {missing}")
    signal_returns = signal_returns[list(REQUIRED_PRODUCTS)]
    gap, intraday = build_continuous_execution_proxy(continuous_raw)
    gap = gap.reindex(index=signal_returns.index, columns=signal_returns.columns)
    intraday = intraday.reindex(index=signal_returns.index, columns=signal_returns.columns)

    lookup = _template_lookup()
    score_streams: dict[str, pd.Series] = {}
    paths: dict[str, pd.DataFrame] = {}
    for template_id in EXECUTION_SELECTION["pool_ids"]:
        weights = specific._template_weight_path(
            signal_returns, lookup[template_id]
        )
        paths[template_id] = weights
        score_streams[template_id] = specific.apply_next_open_product_weights(
            gap,
            intraday,
            weights,
            cost_bps=BASE_COST_BPS,
        )
    return _meta_weight_path(score_streams, paths)


def _window_metrics(series: pd.Series) -> dict[str, dict]:
    return {name: aggressive._window_metrics(series, name) for name in WINDOWS}


def evaluate(specific_raw: pd.DataFrame, continuous_raw: pd.DataFrame) -> dict:
    close_ret, gap_ret, intraday_ret, selections, quality = (
        specific.build_roll_safe_execution_returns(specific_raw)
    )
    weights = generate_execution_signal_weights(continuous_raw)
    weights = weights.reindex(
        index=close_ret.index, columns=close_ret.columns, fill_value=0.0
    ).fillna(0.0)

    close_paths: dict[str, dict] = {}
    execution_paths: dict[str, dict] = {}
    for label, cost in (
        ("base", BASE_COST_BPS),
        ("stress", STRESS_COST_BPS),
        ("extreme", EXTREME_COST_BPS),
    ):
        close_paths[label] = _window_metrics(
            specific.apply_product_weights(close_ret, weights, cost_bps=cost)
        )
        execution_paths[label] = _window_metrics(
            specific.apply_next_open_product_weights(
                gap_ret, intraday_ret, weights, cost_bps=cost
            )
        )

    quality_reasons = [
        f"{product} missing same-contract next return >=5%"
        for product, item in sorted(quality.items())
        if item["missing_next_ratio"] >= 0.05
    ]
    base_recent = execution_paths["base"]["full_recent"]
    stress_recent = execution_paths["stress"]["full_recent"]
    target_pass = bool(
        base_recent["annualized_return"] >= 1.0
        and base_recent["max_drawdown"] > -0.30
        and base_recent["active_days"] >= 50
        and stress_recent["annualized_return"] > 0.0
    )
    reasons = list(quality_reasons)
    if not target_pass:
        reasons.append(
            "execution-aligned roll-safe path does not retain >=100% annualized return with base drawdown <30% and positive stress return"
        )

    report = {
        "source": "AKShare/Sina continuous OHLC signals + concrete daily contract execution",
        "role": "selection-biased execution-aligned roll-safe L4 for aggressive directional production",
        "specific_contracts": True,
        "roll_safe": True,
        "selection_frozen": True,
        "selection_bias_acknowledged": True,
        "pristine_final_oos": PRISTINE_FINAL_OOS,
        "historical_l1_available": False,
        "max_gross_leverage": MAX_GROSS_LEVERAGE,
        "execution_selection": EXECUTION_SELECTION,
        "data_quality": quality,
        "close_to_close_diagnostic": close_paths,
        "next_open_execution": execution_paths,
        "target": {
            "annualized_return": 1.0,
            "max_drawdown": -0.30,
            "stress_must_be_positive": True,
            "gross_leverage_cap": MAX_GROSS_LEVERAGE,
            "target_met": bool(target_pass and not quality_reasons),
            "reasons": reasons,
        },
    }
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    selections.to_csv(output / "execution_aligned_specific_selection.csv", index=False)
    weights.stack().rename("weight").reset_index().query("abs(weight) > 1e-15").to_csv(
        output / "execution_aligned_weights.csv", index=False
    )
    return report


def main() -> None:
    runtime = Path("runtime")
    specific_path = runtime / "return_target_specific_contracts.csv"
    continuous_path = runtime / "broad_daily_universe.csv"
    missing = [str(path) for path in (specific_path, continuous_path) if not path.exists()]
    if missing:
        raise SystemExit(f"execution-aligned L4 inputs missing: {missing}")
    report = evaluate(
        pd.read_csv(specific_path),
        pd.read_csv(continuous_path),
    )
    output = runtime / "execution_aligned_target_report.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "execution_selection": report["execution_selection"],
                "base_full_recent": report["next_open_execution"]["base"]["full_recent"],
                "stress_full_recent": report["next_open_execution"]["stress"]["full_recent"],
                "base_oos": report["next_open_execution"]["base"]["oos"],
                "stress_oos": report["next_open_execution"]["stress"]["oos"],
                "target": report["target"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
