"""Frozen roll-safe L4 for the aggressive 100% return-target candidate.

L3 intentionally fit the already-observed recent window and found a 2x-gross directional
portfolio above 100% annualized return. This module does not reselect that candidate.
It freezes the exact L3 template pool/meta parameters, rebuilds daily returns from
concrete contracts selected point-in-time, and evaluates both:

1. the exact L3 daily product weights applied to roll-safe returns (roll-artifact audit);
2. the exact frozen signal/meta configuration recomputed on the roll-safe return panel.

No L4 metric is allowed to choose a new pool or increase leverage.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_aggressive_directional as aggressive
import fetch_return_target_specific_daily as specific_fetch

MIN_DAYS_TO_DELIVERY = 20
MIN_PRODUCT_DAYS = 700
MAX_GROSS_LEVERAGE = 2.0
PRISTINE_FINAL_OOS = False
REQUIRED_PRODUCTS = tuple(specific_fetch.PRODUCTS)
BASE_COST_BPS = aggressive.BASE_COST_BPS
STRESS_COST_BPS = aggressive.STRESS_COST_BPS
EXTREME_COST_BPS = aggressive.EXTREME_COST_BPS
WINDOWS = aggressive.WINDOWS

FROZEN_SELECTION = {
    "selection_bias": "full_recent_target_fit",
    "pool_size": 24,
    "pool_ids": [
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
    ],
    "meta_lookback": 10,
    "rebalance": 5,
    "count": 2,
    "effective_gross_leverage": 2.0,
}


def build_roll_safe_returns(
    raw: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict]]:
    """Build t-1→t returns on the exact contract selected at t-1."""
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["delivery"] = pd.to_datetime(frame["delivery"], errors="coerce")
    for column in ("close", "volume", "hold"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["product"] = frame["product"].astype(str).str.upper()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame = frame.dropna(
        subset=["date", "delivery", "product", "symbol", "close", "hold"]
    )
    frame = frame[(frame["close"] > 0) & (frame["hold"] >= 0)]
    frame["volume"] = frame["volume"].fillna(0.0)
    frame.drop_duplicates(["product", "symbol", "date"], keep="last", inplace=True)
    frame.sort_values(["product", "date", "symbol"], inplace=True)

    return_series: dict[str, pd.Series] = {}
    selection_rows: list[dict] = []
    quality: dict[str, dict] = {}

    for product in REQUIRED_PRODUCTS:
        product_frame = frame[frame["product"] == product].copy()
        if product_frame.empty:
            raise ValueError(f"return-target L4 missing specific-contract product: {product}")
        by_symbol = {
            str(symbol): group.set_index("date").sort_index()
            for symbol, group in product_frame.groupby("symbol")
        }
        dates = pd.DatetimeIndex(sorted(product_frame["date"].unique()))
        choices: dict[pd.Timestamp, str] = {}

        for trading_day, day_rows in product_frame.groupby("date"):
            trading_day = pd.Timestamp(trading_day)
            eligible = day_rows[
                (day_rows["delivery"] - trading_day).dt.days >= MIN_DAYS_TO_DELIVERY
            ].copy()
            if eligible.empty:
                continue
            eligible.sort_values(
                ["hold", "volume", "delivery", "symbol"],
                ascending=[False, False, True, True],
                inplace=True,
            )
            chosen = eligible.iloc[0]
            symbol = str(chosen["symbol"])
            choices[trading_day] = symbol
            selection_rows.append(
                {
                    "date": trading_day,
                    "product": product,
                    "exchange": str(chosen.get("exchange", specific_fetch.PRODUCT_EXCHANGE[product])),
                    "symbol": symbol,
                    "delivery": pd.Timestamp(chosen["delivery"]),
                    "close": float(chosen["close"]),
                    "volume": float(chosen["volume"]),
                    "open_interest": float(chosen["hold"]),
                    "days_to_delivery": int((pd.Timestamp(chosen["delivery"]) - trading_day).days),
                }
            )

        selected = pd.Series(choices, dtype=object).sort_index()
        valid_days = int(selected.index.nunique())
        if valid_days < MIN_PRODUCT_DAYS:
            raise ValueError(
                f"return-target specific coverage too short for {product}: {valid_days}"
            )

        returns = pd.Series(np.nan, index=dates, dtype=float)
        previous_date: pd.Timestamp | None = None
        previous_symbol: str | None = None
        missing_next = 0
        rolls = 0
        for trading_day in dates:
            trading_day = pd.Timestamp(trading_day)
            if previous_date is not None and previous_symbol is not None:
                symbol_frame = by_symbol.get(previous_symbol)
                realized = np.nan
                if (
                    symbol_frame is not None
                    and previous_date in symbol_frame.index
                    and trading_day in symbol_frame.index
                ):
                    previous_close = float(symbol_frame.loc[previous_date, "close"])
                    current_close = float(symbol_frame.loc[trading_day, "close"])
                    if previous_close > 0 and current_close > 0:
                        realized = current_close / previous_close - 1.0
                if np.isfinite(realized) and abs(realized) <= aggressive.MAX_ABS_DAILY_RETURN:
                    returns.loc[trading_day] = float(realized)
                else:
                    missing_next += 1
            current_symbol = choices.get(trading_day)
            if (
                previous_symbol is not None
                and current_symbol is not None
                and current_symbol != previous_symbol
            ):
                rolls += 1
            previous_date = trading_day
            previous_symbol = current_symbol

        return_series[product] = returns
        quality[product] = {
            "selected_days": valid_days,
            "contracts_used": int(selected.nunique()),
            "rolls": int(rolls),
            "missing_next_contract_returns": int(missing_next),
            "missing_next_ratio": float(missing_next / max(len(dates) - 1, 1)),
        }

    panel = pd.DataFrame(return_series).sort_index()
    selections = pd.DataFrame(selection_rows).sort_values(["date", "product"])
    if selections.empty:
        raise ValueError("return-target specific selection is empty")
    if int(selections["days_to_delivery"].min()) < MIN_DAYS_TO_DELIVERY:
        raise AssertionError("return-target delivery blackout violated")
    return panel, selections, quality


def replay_frozen_weights(
    returns: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    cost_bps: float,
) -> tuple[pd.Series, pd.Series]:
    """Apply exact L3 daily weights to roll-safe returns and charge weight turnover."""
    raw = weights.copy()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["product"] = raw["product"].astype(str).str.upper()
    raw["weight"] = pd.to_numeric(raw["weight"], errors="coerce")
    raw.dropna(subset=["date", "product", "weight"], inplace=True)
    matrix = raw.pivot_table(
        index="date", columns="product", values="weight", aggfunc="sum", fill_value=0.0
    )
    matrix = matrix.reindex(index=returns.index, columns=returns.columns, fill_value=0.0)
    matrix = matrix.fillna(0.0).astype(float)
    gross = matrix.abs().sum(axis=1)
    if bool((gross > MAX_GROSS_LEVERAGE + 1e-10).any()):
        offending = float(gross.max())
        raise ValueError(f"frozen L3 weights exceed 2x gross cap: {offending}")
    realized = returns.reindex_like(matrix).fillna(0.0)
    pnl = (matrix * realized).sum(axis=1)
    turnover = matrix.diff().abs().sum(axis=1)
    if len(matrix):
        turnover.iloc[0] = float(matrix.iloc[0].abs().sum())
    pnl = pnl - turnover * float(cost_bps) / 10000.0
    return pnl.astype(float), gross.astype(float)


def _window_metrics(series: pd.Series) -> dict[str, dict]:
    return {
        name: aggressive._window_metrics(series, name)
        for name in WINDOWS
    }


def _target_pass(base_metrics: dict, stress_metrics: dict) -> bool:
    return bool(
        base_metrics["annualized_return"] >= 1.0
        and base_metrics["max_drawdown"] > -0.30
        and base_metrics["active_days"] >= 50
        and stress_metrics["annualized_return"] > 0.0
    )


def _validate_frozen_l3_report(report: dict) -> None:
    selection = report.get("selection", {})
    for key in ("selection_bias", "pool_size", "meta_lookback", "rebalance", "count"):
        if selection.get(key) != FROZEN_SELECTION[key]:
            raise ValueError(
                f"L3 frozen selection drifted for {key}: {selection.get(key)!r} != {FROZEN_SELECTION[key]!r}"
            )
    if list(selection.get("pool_ids", [])) != list(FROZEN_SELECTION["pool_ids"]):
        raise ValueError("L3 frozen template pool drifted; refusing L4 reselection")
    if float(selection.get("effective_gross_leverage", 0.0)) > MAX_GROSS_LEVERAGE:
        raise ValueError("L3 selection exceeds L4 gross leverage cap")


def recompute_frozen_configuration(
    returns: pd.DataFrame,
    *,
    cost_bps: float,
) -> pd.Series:
    """Recompute only the already-frozen pool/meta policy on roll-safe returns."""
    template_lookup = {
        aggressive.template_id(template): template
        for template in aggressive.directional_templates()
    }
    pool_ids = list(FROZEN_SELECTION["pool_ids"])
    missing = [name for name in pool_ids if name not in template_lookup]
    if missing:
        raise ValueError(f"frozen L3 templates no longer exist: {missing}")

    streams: dict[str, pd.Series] = {}
    # Missing same-contract returns are explicitly zero-realized days for the research
    # signal path, while data quality separately reports every gap. This avoids a single
    # missing quote invalidating a 120-day rolling feature for months.
    signal_returns = returns.fillna(0.0)
    for name in pool_ids:
        gross_pnl, turnover, _ = aggressive._simulate_arrays(
            signal_returns,
            template_lookup[name],
            record_weights=False,
        )
        streams[name] = aggressive.apply_cost(gross_pnl, turnover, cost_bps)
    series, _ = aggressive._meta_rotate(
        streams,
        meta_lookback=int(FROZEN_SELECTION["meta_lookback"]),
        rebalance=int(FROZEN_SELECTION["rebalance"]),
        count=int(FROZEN_SELECTION["count"]),
        switch_cost_bps=cost_bps,
    )
    return series


def evaluate(
    specific_raw: pd.DataFrame,
    frozen_weights: pd.DataFrame,
    l3_report: dict,
) -> dict:
    _validate_frozen_l3_report(l3_report)
    returns, selections, data_quality = build_roll_safe_returns(specific_raw)

    frozen_paths = {}
    frozen_gross = None
    for label, cost in (
        ("base", BASE_COST_BPS),
        ("stress", STRESS_COST_BPS),
        ("extreme", EXTREME_COST_BPS),
    ):
        series, gross = replay_frozen_weights(returns, frozen_weights, cost_bps=cost)
        frozen_paths[label] = _window_metrics(series)
        if frozen_gross is None:
            frozen_gross = gross

    recomputed_paths = {}
    for label, cost in (
        ("base", BASE_COST_BPS),
        ("stress", STRESS_COST_BPS),
        ("extreme", EXTREME_COST_BPS),
    ):
        series = recompute_frozen_configuration(returns, cost_bps=cost)
        recomputed_paths[label] = _window_metrics(series)

    quality_reasons = []
    for product, item in sorted(data_quality.items()):
        if item["selected_days"] < MIN_PRODUCT_DAYS:
            quality_reasons.append(f"{product} selected-day coverage below {MIN_PRODUCT_DAYS}")
        if item["missing_next_ratio"] >= 0.05:
            quality_reasons.append(f"{product} missing same-contract next return >=5%")

    frozen_pass = _target_pass(
        frozen_paths["base"]["full_recent"],
        frozen_paths["stress"]["full_recent"],
    )
    recomputed_pass = _target_pass(
        recomputed_paths["base"]["full_recent"],
        recomputed_paths["stress"]["full_recent"],
    )
    reasons: list[str] = list(quality_reasons)
    if not frozen_pass:
        reasons.append("exact L3 daily weights do not retain the 100% roll-safe target")
    if not recomputed_pass:
        reasons.append("frozen L3 signal/meta configuration does not retain the 100% roll-safe target")

    report = {
        "source": "AKShare/Sina concrete futures daily bars",
        "role": "selection-biased but roll-safe L4 validation of frozen aggressive directional target",
        "specific_contracts": True,
        "roll_safe": True,
        "selection_frozen": True,
        "historical_l1_available": False,
        "pristine_final_oos": PRISTINE_FINAL_OOS,
        "max_gross_leverage": MAX_GROSS_LEVERAGE,
        "min_days_to_delivery": MIN_DAYS_TO_DELIVERY,
        "frozen_selection": FROZEN_SELECTION,
        "data_quality": data_quality,
        "frozen_weight_replay": frozen_paths,
        "recomputed_roll_safe": recomputed_paths,
        "target": {
            "annualized_return": 1.0,
            "max_drawdown": -0.30,
            "stress_must_be_positive": True,
            "gross_leverage_cap": MAX_GROSS_LEVERAGE,
            "frozen_weight_target_met": frozen_pass,
            "recomputed_target_met": recomputed_pass,
            "target_met": bool(frozen_pass and recomputed_pass and not quality_reasons),
            "reasons": reasons,
        },
    }

    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    selections.to_csv(output / "return_target_specific_selection.csv", index=False)
    return report


def main() -> None:
    runtime = Path("runtime")
    specific_path = runtime / "return_target_specific_contracts.csv"
    weights_path = runtime / "aggressive_directional_weights.csv"
    l3_path = runtime / "aggressive_directional_report.json"
    if not specific_path.exists() or not weights_path.exists() or not l3_path.exists():
        raise SystemExit("L4 inputs missing; rebuild frozen L3 evidence and specific contracts first")
    report = evaluate(
        pd.read_csv(specific_path),
        pd.read_csv(weights_path),
        json.loads(l3_path.read_text(encoding="utf-8")),
    )
    output = runtime / "return_target_specific_report.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "data_quality": report["data_quality"],
                "frozen_weight_replay": report["frozen_weight_replay"],
                "recomputed_roll_safe": report["recomputed_roll_safe"],
                "target": report["target"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
