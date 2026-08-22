"""Roll-safe L4 for the selection-biased 100% return target.

The frozen aggressive policy uses AKShare/Sina continuous commodity series only as a
causal daily signal feed. Product weights are then mapped to point-in-time concrete
contracts, and t-1→t PnL is always earned on the exact contract selected at t-1.

The important accounting boundary is the *final product portfolio*. Template-level
returns are used only to rank the frozen meta policy. Trading cost is charged once on
actual final product-weight turnover; unselected template churn is not a real order and
must not be charged as if it were. Gross exposure is capped at 2x throughout.
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
    """Build t-1→t returns on the exact eligible contract selected at t-1."""
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
            raise ValueError(f"return-target L4 missing product: {product}")
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
                    "exchange": str(
                        chosen.get(
                            "exchange", specific_fetch.PRODUCT_EXCHANGE[product]
                        )
                    ),
                    "symbol": symbol,
                    "delivery": pd.Timestamp(chosen["delivery"]),
                    "close": float(chosen["close"]),
                    "volume": float(chosen["volume"]),
                    "open_interest": float(chosen["hold"]),
                    "days_to_delivery": int(
                        (pd.Timestamp(chosen["delivery"]) - trading_day).days
                    ),
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
                if (
                    np.isfinite(realized)
                    and abs(realized) <= aggressive.MAX_ABS_DAILY_RETURN
                ):
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
            "missing_next_ratio": float(
                missing_next / max(len(dates) - 1, 1)
            ),
        }

    panel = pd.DataFrame(return_series).sort_index()
    selections = pd.DataFrame(selection_rows).sort_values(["date", "product"])
    if selections.empty:
        raise ValueError("return-target specific selection is empty")
    if int(selections["days_to_delivery"].min()) < MIN_DAYS_TO_DELIVERY:
        raise AssertionError("return-target delivery blackout violated")
    return panel, selections, quality


def _long_weights_to_matrix(
    weights: pd.DataFrame,
    *,
    index: pd.Index,
    columns: pd.Index,
) -> pd.DataFrame:
    raw = weights.copy()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["product"] = raw["product"].astype(str).str.upper()
    raw["weight"] = pd.to_numeric(raw["weight"], errors="coerce")
    raw.dropna(subset=["date", "product", "weight"], inplace=True)
    matrix = raw.pivot_table(
        index="date",
        columns="product",
        values="weight",
        aggfunc="sum",
        fill_value=0.0,
    )
    return matrix.reindex(index=index, columns=columns, fill_value=0.0).fillna(0.0)


def apply_product_weights(
    returns: pd.DataFrame,
    matrix: pd.DataFrame,
    *,
    cost_bps: float,
) -> tuple[pd.Series, pd.Series]:
    """Realize a final product portfolio and charge actual final turnover once."""
    matrix = matrix.reindex(
        index=returns.index, columns=returns.columns, fill_value=0.0
    ).fillna(0.0)
    gross = matrix.abs().sum(axis=1)
    if bool((gross > MAX_GROSS_LEVERAGE + 1e-10).any()):
        raise ValueError(
            f"product weights exceed 2x gross cap: {float(gross.max())}"
        )
    realized = returns.reindex_like(matrix).fillna(0.0)
    pnl = (matrix * realized).sum(axis=1)
    turnover = matrix.diff().abs().sum(axis=1)
    if len(matrix):
        turnover.iloc[0] = float(matrix.iloc[0].abs().sum())
    pnl -= turnover * float(cost_bps) / 10000.0
    return pnl.astype(float), gross.astype(float)


def replay_frozen_weights(
    returns: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    cost_bps: float,
) -> tuple[pd.Series, pd.Series]:
    matrix = _long_weights_to_matrix(
        weights, index=returns.index, columns=returns.columns
    )
    return apply_product_weights(returns, matrix, cost_bps=cost_bps)


def _template_weight_path(
    signal_returns: pd.DataFrame,
    template: aggressive.DirectionalTemplate,
) -> pd.DataFrame:
    scores = aggressive.signal_scores(signal_returns, template).to_numpy(float)
    weights = np.zeros(signal_returns.shape[1], dtype=float)
    audit = np.zeros(signal_returns.shape, dtype=float)
    step = max(int(template.rebalance), 1)
    for position in range(1, len(signal_returns)):
        if position % step == 0:
            lagged = scores[position - 1]
            valid = np.flatnonzero(
                np.isfinite(lagged) & (np.abs(lagged) > 1e-12)
            )
            next_weights = np.zeros(signal_returns.shape[1], dtype=float)
            if valid.size:
                order = valid[
                    np.argsort(-np.abs(lagged[valid]), kind="stable")
                ]
                selected = order[
                    : min(int(template.max_products), len(order))
                ]
                if selected.size:
                    each = min(
                        float(template.gross_leverage), MAX_GROSS_LEVERAGE
                    ) / float(selected.size)
                    next_weights[selected] = np.sign(lagged[selected]) * each
            weights = next_weights
        audit[position] = weights
    frame = pd.DataFrame(
        audit, index=signal_returns.index, columns=signal_returns.columns
    )
    if float(frame.abs().sum(axis=1).max()) > MAX_GROSS_LEVERAGE + 1e-10:
        raise AssertionError("template weight path exceeds gross leverage cap")
    return frame


def simulate_frozen_continuous_signals(
    signal_returns: pd.DataFrame,
    realized_returns: pd.DataFrame,
    template: aggressive.DirectionalTemplate,
    *,
    cost_bps: float,
    return_audit: bool = False,
):
    """Unit-level dual-source simulation for one frozen template."""
    signal = signal_returns.astype(float).replace([np.inf, -np.inf], np.nan)
    matrix = _template_weight_path(signal, template)
    series, _ = apply_product_weights(
        realized_returns.reindex(index=signal.index, columns=signal.columns),
        matrix,
        cost_bps=cost_bps,
    )
    return (series, matrix) if return_audit else series


def _frozen_templates() -> dict[str, aggressive.DirectionalTemplate]:
    lookup = {
        aggressive.template_id(template): template
        for template in aggressive.directional_templates()
    }
    missing = [
        template_id
        for template_id in FROZEN_SELECTION["pool_ids"]
        if template_id not in lookup
    ]
    if missing:
        raise ValueError(f"frozen L3 templates no longer exist: {missing}")
    return {
        template_id: lookup[template_id]
        for template_id in FROZEN_SELECTION["pool_ids"]
    }


def _meta_weight_path(
    score_streams: dict[str, pd.Series],
    template_weights: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Select templates causally, then average their *product weights*."""
    score_frame = pd.DataFrame(score_streams).sort_index().fillna(0.0)
    names = list(score_frame.columns)
    scores = aggressive._trailing_score(
        score_frame, int(FROZEN_SELECTION["meta_lookback"])
    )
    example = template_weights[names[0]].reindex(score_frame.index).fillna(0.0)
    final = pd.DataFrame(0.0, index=score_frame.index, columns=example.columns)
    selected: list[int] = []
    rebalance = max(int(FROZEN_SELECTION["rebalance"]), 1)
    count = int(FROZEN_SELECTION["count"])
    lookback = int(FROZEN_SELECTION["meta_lookback"])
    for position, timestamp in enumerate(score_frame.index):
        if position >= lookback and (
            not selected or position % rebalance == 0
        ):
            row = scores[position]
            valid = np.flatnonzero(np.isfinite(row))
            selected = (
                [
                    int(index)
                    for index in valid[
                        np.argsort(-row[valid], kind="stable")
                    ][:count]
                ]
                if valid.size
                else []
            )
        if selected:
            rows = [
                template_weights[names[index]].loc[timestamp]
                for index in selected
            ]
            final.loc[timestamp] = pd.concat(rows, axis=1).mean(axis=1)
    gross = final.abs().sum(axis=1)
    if float(gross.max()) > MAX_GROSS_LEVERAGE + 1e-10:
        raise AssertionError("meta product weights exceed gross leverage cap")
    return final


def generate_continuous_signal_weights(
    continuous_raw: pd.DataFrame,
) -> pd.DataFrame:
    """Recreate the exact frozen L3 daily product-weight policy causally."""
    signal_returns, _ = aggressive.build_return_panel(continuous_raw)
    signal_returns.columns = [
        str(column).upper() for column in signal_returns.columns
    ]
    missing = sorted(set(REQUIRED_PRODUCTS) - set(signal_returns.columns))
    if missing:
        raise ValueError(f"continuous signal feed missing products: {missing}")
    signal_returns = signal_returns[list(REQUIRED_PRODUCTS)]

    score_streams: dict[str, pd.Series] = {}
    template_weights: dict[str, pd.DataFrame] = {}
    for template_id, template in _frozen_templates().items():
        gross_pnl, turnover, _ = aggressive._simulate_arrays(
            signal_returns, template, record_weights=False
        )
        # The frozen L3 meta rank used base-cost theoretical continuous PnL.
        score_streams[template_id] = aggressive.apply_cost(
            gross_pnl, turnover, BASE_COST_BPS
        )
        template_weights[template_id] = _template_weight_path(
            signal_returns, template
        )
    return _meta_weight_path(score_streams, template_weights)


def recompute_continuous_signal_roll_safe(
    continuous_raw: pd.DataFrame,
    realized_returns: pd.DataFrame,
    *,
    cost_bps: float,
) -> tuple[pd.Series, pd.DataFrame]:
    weights = generate_continuous_signal_weights(continuous_raw)
    realized = realized_returns.reindex(
        index=weights.index, columns=weights.columns
    )
    series, _ = apply_product_weights(
        realized, weights, cost_bps=cost_bps
    )
    return series, weights


def _window_metrics(series: pd.Series) -> dict[str, dict]:
    return {
        name: aggressive._window_metrics(series, name) for name in WINDOWS
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
    for key in (
        "selection_bias",
        "pool_size",
        "meta_lookback",
        "rebalance",
        "count",
    ):
        if selection.get(key) != FROZEN_SELECTION[key]:
            raise ValueError(
                f"L3 frozen selection drifted for {key}: "
                f"{selection.get(key)!r} != {FROZEN_SELECTION[key]!r}"
            )
    if list(selection.get("pool_ids", [])) != list(FROZEN_SELECTION["pool_ids"]):
        raise ValueError("L3 frozen template pool drifted; refusing L4 reselection")
    if float(selection.get("effective_gross_leverage", 0.0)) > MAX_GROSS_LEVERAGE:
        raise ValueError("L3 selection exceeds L4 gross leverage cap")


def _weight_match(
    generated: pd.DataFrame,
    frozen_long: pd.DataFrame,
) -> dict:
    frozen = _long_weights_to_matrix(
        frozen_long, index=generated.index, columns=generated.columns
    )
    diff = (generated - frozen).abs()
    max_diff = float(diff.to_numpy().max()) if diff.size else 0.0
    exact_dates = float((diff.max(axis=1) <= 1e-12).mean()) if len(diff) else 1.0
    return {
        "max_abs_diff": max_diff,
        "mean_abs_diff": float(diff.to_numpy().mean()) if diff.size else 0.0,
        "exact_date_ratio": exact_dates,
        "exact": bool(max_diff <= 1e-12),
    }


def evaluate(
    specific_raw: pd.DataFrame,
    continuous_raw: pd.DataFrame,
    frozen_weights: pd.DataFrame,
    l3_report: dict,
) -> dict:
    _validate_frozen_l3_report(l3_report)
    returns, selections, data_quality = build_roll_safe_returns(specific_raw)
    generated_weights = generate_continuous_signal_weights(continuous_raw)
    weight_match = _weight_match(generated_weights, frozen_weights)

    frozen_paths: dict[str, dict] = {}
    continuous_paths: dict[str, dict] = {}
    for label, cost in (
        ("base", BASE_COST_BPS),
        ("stress", STRESS_COST_BPS),
        ("extreme", EXTREME_COST_BPS),
    ):
        frozen_series, _ = replay_frozen_weights(
            returns, frozen_weights, cost_bps=cost
        )
        frozen_paths[label] = _window_metrics(frozen_series)
        continuous_series, _ = recompute_continuous_signal_roll_safe(
            continuous_raw, returns, cost_bps=cost
        )
        continuous_paths[label] = _window_metrics(continuous_series)

    quality_reasons: list[str] = []
    for product, item in sorted(data_quality.items()):
        if item["selected_days"] < MIN_PRODUCT_DAYS:
            quality_reasons.append(
                f"{product} selected-day coverage below {MIN_PRODUCT_DAYS}"
            )
        if item["missing_next_ratio"] >= 0.05:
            quality_reasons.append(
                f"{product} missing same-contract next return >=5%"
            )
    if not weight_match["exact"]:
        quality_reasons.append(
            "recomputed continuous-signal weights do not exactly reproduce frozen L3"
        )

    frozen_pass = _target_pass(
        frozen_paths["base"]["full_recent"],
        frozen_paths["stress"]["full_recent"],
    )
    continuous_pass = _target_pass(
        continuous_paths["base"]["full_recent"],
        continuous_paths["stress"]["full_recent"],
    )
    reasons: list[str] = list(quality_reasons)
    if not frozen_pass:
        reasons.append(
            "exact L3 daily weights do not retain the roll-safe 100% target"
        )
    if not continuous_pass:
        reasons.append(
            "continuous-signal/concrete-PnL recomputation does not retain the 100% target"
        )

    report = {
        "source": (
            "AKShare/Sina continuous daily signal feed + concrete futures daily realized PnL"
        ),
        "role": (
            "selection-biased, causally reproducible L4 mapping from continuous signals "
            "to roll-safe concrete-contract execution"
        ),
        "specific_contracts": True,
        "roll_safe": True,
        "selection_frozen": True,
        "signal_source": "AKShare/Sina continuous daily commodity series",
        "realized_pnl_source": "same concrete contract selected at prior close",
        "historical_l1_available": False,
        "pristine_final_oos": PRISTINE_FINAL_OOS,
        "max_gross_leverage": MAX_GROSS_LEVERAGE,
        "min_days_to_delivery": MIN_DAYS_TO_DELIVERY,
        "frozen_selection": FROZEN_SELECTION,
        "generated_weight_match": weight_match,
        "data_quality": data_quality,
        "frozen_weight_replay": frozen_paths,
        "continuous_signal_roll_safe": continuous_paths,
        "target": {
            "annualized_return": 1.0,
            "max_drawdown": -0.30,
            "stress_must_be_positive": True,
            "gross_leverage_cap": MAX_GROSS_LEVERAGE,
            "frozen_weight_target_met": frozen_pass,
            "continuous_signal_target_met": continuous_pass,
            "target_met": bool(
                frozen_pass and continuous_pass and not quality_reasons
            ),
            "reasons": reasons,
        },
    }
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    selections.to_csv(
        output / "return_target_specific_selection.csv", index=False
    )
    return report


def main() -> None:
    runtime = Path("runtime")
    specific_path = runtime / "return_target_specific_contracts.csv"
    continuous_path = runtime / "broad_daily_universe.csv"
    weights_path = runtime / "aggressive_directional_weights.csv"
    l3_path = runtime / "aggressive_directional_report.json"
    required = (specific_path, continuous_path, weights_path, l3_path)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"L4 inputs missing: {missing}")
    report = evaluate(
        pd.read_csv(specific_path),
        pd.read_csv(continuous_path),
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
                "generated_weight_match": report["generated_weight_match"],
                "data_quality": report["data_quality"],
                "frozen_weight_replay": report["frozen_weight_replay"],
                "continuous_signal_roll_safe": report[
                    "continuous_signal_roll_safe"
                ],
                "target": report["target"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
