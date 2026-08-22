"""Corrected roll-safe structural L4 evaluator.

Physical production-margin signals use actual selected concrete-contract closes. Any
component roll closes the physical structure and resets the complete formation window.
Return t->t+1 remains measured on the same contract chosen at close t. Statistical soy
and BU/FU residuals reuse the previously tested causal implementations on roll-safe
rebased indexes.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import _structural_rotation_legacy as legacy

WINDOWS = legacy.WINDOWS
MIN_DAYS_TO_DELIVERY = legacy.MIN_DAYS_TO_DELIVERY
MIN_PRODUCT_DAYS = legacy.MIN_PRODUCT_DAYS
BASE_COST_BPS = legacy.BASE_COST_BPS
STRESS_COST_BPS = legacy.STRESS_COST_BPS
EXTREME_COST_BPS = legacy.EXTREME_COST_BPS
MAX_GROSS_LEVERAGE = legacy.MAX_GROSS_LEVERAGE
LEVERAGE_GRID = legacy.LEVERAGE_GRID
MAX_CALIBRATION_DRAWDOWN = legacy.MAX_CALIBRATION_DRAWDOWN
MAX_TARGET_DRAWDOWN = legacy.MAX_TARGET_DRAWDOWN
MAX_MARGIN_RATIO_PROXY = legacy.MAX_MARGIN_RATIO_PROXY
MARGIN_PROXY = legacy.MARGIN_PROXY
QUALITY_WEIGHT = legacy.QUALITY_WEIGHT
REQUIRED_PRODUCTS = legacy.REQUIRED_PRODUCTS
MeanReversionProfile = legacy.MeanReversionProfile
STEEL_PROFILE = legacy.STEEL_PROFILE
COKE_PROFILE = legacy.COKE_PROFILE
SOY_PROFILE = legacy.SOY_PROFILE
BUFU_PROFILE = legacy.BUFU_PROFILE
_metrics = legacy._metrics
_window_metrics = legacy._window_metrics
_qualifies = legacy._qualifies
_half_life = legacy._half_life
_soy_path = legacy._soy_path
_bufu_path = legacy._bufu_path
_selected_candidate = legacy._selected_candidate
_rotation = legacy._rotation


def build_roll_safe_panel(raw: pd.DataFrame):
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["delivery"] = pd.to_datetime(frame["delivery"], errors="coerce")
    for column in ("close", "volume", "hold"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=["date", "delivery", "product", "symbol", "close", "hold"]
    )
    frame = frame[(frame["close"] > 0) & (frame["hold"] >= 0)]
    frame.drop_duplicates(["product", "symbol", "date"], keep="last", inplace=True)
    frame.sort_values(["product", "date", "symbol"], inplace=True)

    index_series: dict[str, pd.Series] = {}
    return_series: dict[str, pd.Series] = {}
    actual_close_series: dict[str, pd.Series] = {}
    selected_symbol_series: dict[str, pd.Series] = {}
    selected_rows: list[dict] = []
    quality: dict[str, dict] = {}

    for product in REQUIRED_PRODUCTS:
        product_frame = frame[frame["product"] == product].copy()
        if product_frame.empty:
            raise ValueError(f"structural specific-contract data missing product: {product}")
        by_symbol = {
            str(symbol): group.set_index("date").sort_index()
            for symbol, group in product_frame.groupby("symbol")
        }
        dates = pd.DatetimeIndex(sorted(product_frame["date"].unique()))
        choices: dict[pd.Timestamp, str] = {}
        selected_closes: dict[pd.Timestamp, float] = {}
        for trading_day, day_rows in product_frame.groupby("date"):
            trading_day = pd.Timestamp(trading_day)
            eligible = day_rows[
                (day_rows["delivery"] - trading_day).dt.days >= MIN_DAYS_TO_DELIVERY
            ].copy()
            if eligible.empty:
                continue
            eligible["volume"] = eligible["volume"].fillna(0.0)
            eligible.sort_values(
                ["hold", "volume", "delivery", "symbol"],
                ascending=[False, False, True, True],
                inplace=True,
            )
            chosen = eligible.iloc[0]
            choices[trading_day] = str(chosen["symbol"])
            selected_closes[trading_day] = float(chosen["close"])
            selected_rows.append(
                {
                    "date": trading_day,
                    "product": product,
                    "symbol": str(chosen["symbol"]),
                    "delivery": pd.Timestamp(chosen["delivery"]),
                    "close": float(chosen["close"]),
                    "open_interest": float(chosen["hold"]),
                    "days_to_delivery": int(
                        (pd.Timestamp(chosen["delivery"]) - trading_day).days
                    ),
                }
            )

        selected = pd.Series(choices, dtype=object).sort_index()
        selected_close = pd.Series(selected_closes, dtype=float).sort_index()
        all_dates = dates.union(selected.index).sort_values()
        synthetic = pd.Series(np.nan, index=all_dates, dtype=float)
        tradable = pd.Series(np.nan, index=all_dates, dtype=float)
        current_index = 100.0
        previous_date: pd.Timestamp | None = None
        previous_symbol: str | None = None
        rolls = 0
        missing_next = 0
        for trading_day in all_dates:
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
                if np.isfinite(realized) and abs(realized) <= 0.20:
                    tradable.loc[trading_day] = float(realized)
                    current_index *= 1.0 + float(realized)
                else:
                    missing_next += 1
            synthetic.loc[trading_day] = current_index
            current_symbol = choices.get(trading_day)
            if (
                previous_symbol is not None
                and current_symbol is not None
                and current_symbol != previous_symbol
            ):
                rolls += 1
            previous_symbol = current_symbol
            previous_date = trading_day

        valid_days = int(selected.index.nunique())
        if valid_days < MIN_PRODUCT_DAYS:
            raise ValueError(
                f"structural specific-contract coverage too short for {product}: {valid_days}"
            )
        index_series[product] = synthetic
        return_series[product] = tradable
        actual_close_series[product] = selected_close.reindex(all_dates)
        selected_symbol_series[product] = selected.reindex(all_dates)
        quality[product] = {
            "selected_days": valid_days,
            "contracts_used": int(selected.nunique()),
            "rolls": int(rolls),
            "missing_next_contract_returns": int(missing_next),
            "missing_next_ratio": float(
                missing_next / max(len(all_dates) - 1, 1)
            ),
        }

    close = pd.DataFrame(index_series).sort_index()
    returns = pd.DataFrame(return_series).reindex(close.index)
    actual_close = pd.DataFrame(actual_close_series).reindex(close.index)
    selected_symbols = pd.DataFrame(selected_symbol_series).reindex(close.index)
    selections = pd.DataFrame(selected_rows).sort_values(["date", "product"])
    if int(selections["days_to_delivery"].min()) < MIN_DAYS_TO_DELIVERY:
        raise AssertionError("structural delivery blackout violated")
    return close, returns, actual_close, selected_symbols, selections, quality

def _stable_segment_mask(
    symbols: pd.DataFrame, names: list[str], formation: int
) -> pd.Series:
    valid = symbols[names].notna().all(axis=1)
    signature = symbols[names].fillna("").astype(str).agg("|".join, axis=1)
    changed = signature.ne(signature.shift()) | ~valid
    segment = changed.cumsum()
    age = segment.groupby(segment).cumcount() + 1
    return valid & (age > formation)


def _physical_path(
    close: pd.DataFrame,
    returns: pd.DataFrame,
    actual_close: pd.DataFrame,
    selected_symbols: pd.DataFrame,
    coefficients: dict[str, float],
    profile: MeanReversionProfile,
) -> pd.DataFrame:
    index = close.index
    names = list(coefficients)
    coefficient = np.asarray([coefficients[name] for name in names], float)
    prices = actual_close[names].to_numpy(float)
    leg_returns = returns[names].to_numpy(float)
    spread = pd.Series(prices @ coefficient, index=index)
    valid_signature = selected_symbols[names].notna().all(axis=1)
    signature = selected_symbols[names].fillna("").astype(str).agg("|".join, axis=1)
    segment = (signature.ne(signature.shift()) | ~valid_signature).cumsum()
    reference_mean = spread.groupby(segment).transform(
        lambda values: values.rolling(
            profile.formation, min_periods=profile.formation
        ).mean().shift(1)
    )
    reference_std = spread.groupby(segment).transform(
        lambda values: values.rolling(
            profile.formation, min_periods=profile.formation
        ).std(ddof=0).shift(1)
    )
    zscore = (spread - reference_mean) / reference_std
    stable = _stable_segment_mask(selected_symbols, names, profile.formation)

    spread_return_values = np.full(len(index), np.nan)
    for position_index in range(1, len(index)):
        previous_prices = prices[position_index - 1]
        next_leg_returns = leg_returns[position_index]
        if np.all(np.isfinite(previous_prices)) and np.all(np.isfinite(next_leg_returns)):
            raw = coefficient * previous_prices
            gross = np.abs(raw).sum()
            if gross > 0:
                spread_return_values[position_index] = float((raw / gross) @ next_leg_returns)
    spread_return = pd.Series(spread_return_values, index=index)
    fast_vol = spread_return.rolling(20, min_periods=20).std().shift(1)
    slow_vol = spread_return.rolling(
        profile.formation, min_periods=max(20, profile.formation // 2)
    ).std().shift(1)
    volatility_ratio = fast_vol / slow_vol

    direction = np.zeros(len(index), dtype=int)
    score = np.zeros(len(index), dtype=float)
    next_return = np.zeros(len(index), dtype=float)
    state = 0
    entry_index = -1
    entry_signature: str | None = None
    weights: np.ndarray | None = None
    spread_values = spread.to_numpy(float)
    z_values = zscore.to_numpy(float)
    volatility_values = volatility_ratio.to_numpy(float)
    stable_values = stable.to_numpy(bool)
    signatures = signature.to_numpy(object)

    for position_index in range(len(index) - 1):
        current_z = z_values[position_index]
        if state == 0:
            history = spread_values[position_index - profile.formation:position_index]
            half_life = _half_life(history[np.isfinite(history)]) if stable_values[position_index] else 999.0
            eligible = (
                stable_values[position_index]
                and np.isfinite(current_z)
                and 2.0 <= half_life <= profile.max_half_life
                and np.isfinite(volatility_values[position_index])
                and volatility_values[position_index] >= profile.min_volatility_ratio
            )
            state = -1 if eligible and current_z >= profile.entry_z else (1 if eligible and current_z <= -profile.entry_z else 0)
            if state:
                entry_index = position_index
                entry_signature = str(signatures[position_index])
                raw = state * coefficient * prices[position_index]
                gross = np.abs(raw).sum()
                weights = raw / gross if gross > 0 else None
                if weights is None:
                    state = 0
                    entry_index = -1
                    entry_signature = None
        else:
            holding = position_index - entry_index
            rolled = (
                entry_signature is None
                or str(signatures[position_index]) != entry_signature
            )
            if (
                rolled
                or not np.isfinite(current_z)
                or abs(current_z) <= profile.exit_z
                or abs(current_z) >= profile.stop_x
                or holding >= profile.max_holding_days
            ):
                state = 0
                entry_index = -1
                entry_signature = None
                weights = None

        if state and weights is not None:
            direction[position_index] = state
            score[position_index] = abs(current_z)
            next_leg_returns = leg_returns[position_index + 1]
            if np.all(np.isfinite(next_leg_returns)):
                next_return[position_index + 1] = float(
                    weights @ next_leg_returns
                )

    return pd.DataFrame(
        {
            "direction": direction,
            "score": score,
            "next_return": next_return,
        },
        index=index,
    )


def build_paths(
    close: pd.DataFrame,
    returns: pd.DataFrame,
    actual_close: pd.DataFrame,
    selected_symbols: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "steel": _physical_path(
            close,
            returns,
            actual_close,
            selected_symbols,
            {"RB": 1.0, "I": -1.6, "J": -0.5},
            STEEL_PROFILE,
        ),
        "coke": _physical_path(
            close,
            returns,
            actual_close,
            selected_symbols,
            {"J": 1.0, "JM": -1.3},
            COKE_PROFILE,
        ),
        "soy": _soy_path(close, returns),
        "bufu": _bufu_path(close, returns),
    }


def _choose_leverage(paths: dict[str, pd.DataFrame]) -> float:
    selected = 0.0
    start, end = WINDOWS["selection_full"]
    for leverage in LEVERAGE_GRID:
        if leverage > MAX_GROSS_LEVERAGE:
            continue
        if any(
            leverage * margin > MAX_MARGIN_RATIO_PROXY
            for margin in MARGIN_PROXY.values()
        ):
            continue
        stressed = _rotation(
            paths, cost_bps=STRESS_COST_BPS, leverage=leverage
        )
        calibration = stressed.loc[
            pd.Timestamp(start) : pd.Timestamp(end)
        ]
        item = _metrics(calibration)
        if (
            item["annualized_return"] > 0.0
            and item["max_drawdown"] > MAX_CALIBRATION_DRAWDOWN
            and bool((calibration > -1.0).all())
        ):
            selected = leverage
    return selected


def evaluate(raw: pd.DataFrame) -> dict:
    (
        close,
        returns,
        actual_close,
        selected_symbols,
        selections,
        data_quality,
    ) = build_roll_safe_panel(raw)
    paths = build_paths(close, returns, actual_close, selected_symbols)
    stress_unlevered = _rotation(
        paths, cost_bps=STRESS_COST_BPS, leverage=1.0
    )
    pre_oos = {
        name: _window_metrics(stress_unlevered, name)
        for name in ("prior1", "prior2", "train", "validation")
    }
    pre_oos_pass = all(_qualifies(item) for item in pre_oos.values())
    leverage = _choose_leverage(paths) if pre_oos_pass else 0.0

    zero = stress_unlevered * 0.0
    base = (
        _rotation(paths, cost_bps=BASE_COST_BPS, leverage=leverage)
        if leverage
        else zero
    )
    stress = (
        _rotation(paths, cost_bps=STRESS_COST_BPS, leverage=leverage)
        if leverage
        else zero
    )
    extreme = (
        _rotation(paths, cost_bps=EXTREME_COST_BPS, leverage=leverage)
        if leverage
        else zero
    )
    stress_oos = _window_metrics(stress, "oos")
    stress_recent = _window_metrics(stress, "full_recent")
    extreme_recent = _window_metrics(extreme, "full_recent")

    reasons: list[str] = []
    if not pre_oos_pass:
        reasons.append(
            "specific-contract structural rotation fails a pre-OOS gate"
        )
    if leverage <= 0.0:
        reasons.append(
            "no leverage level satisfies pre-OOS drawdown and margin gates"
        )
    if stress_oos["annualized_return"] <= 0.0:
        reasons.append("stress-cost Final OOS return is not positive")
    if stress_recent["annualized_return"] < 1.0:
        reasons.append(
            "stress-cost roll-safe two-year annualized return is below 100%"
        )
    if stress_recent["max_drawdown"] <= MAX_TARGET_DRAWDOWN:
        reasons.append(
            "stress-cost roll-safe two-year drawdown exceeds 20%"
        )
    if extreme_recent["annualized_return"] <= 0.0:
        reasons.append("30bp extreme-cost two-year return is not positive")
    if extreme_recent["max_drawdown"] <= -0.30:
        reasons.append("30bp extreme-cost drawdown exceeds 30%")

    report = {
        "source": "AKShare/Sina concrete futures daily bars",
        "role": (
            "L4 roll-safe structural rotation evidence; historical L1/depth "
            "unavailable"
        ),
        "specific_contracts": True,
        "roll_safe": True,
        "historical_l1_available": False,
        "pristine_final_oos": False,
        "physical_signal_prices": (
            "actual selected concrete-contract closes; full formation resets "
            "when any physical leg rolls"
         ),
        "required_products": list(REQUIRED_PRODUCTS),
        "max_active_structures": 1,
        "quality_weight_source": "frozen pre-OOS L3 worst-Sharpe values",
        "quality_weights": QUALITY_WEIGHT,
        "base_cost_bps_one_way": BASE_COST_BPS,
        "stress_cost_bps_one_way": STRESS_COST_BPS,
        "extreme_cost_bps_one_way": EXTREME_COST_BPS,
        "max_gross_leverage": MAX_GROSS_LEVERAGE,
        "selected_leverage": leverage,
        "max_margin_ratio_proxy": MAX_MARGIN_RATIO_PROXY,
        "margin_proxy": MARGIN_PROXY,
        "data_quality": data_quality,
        "pre_oos": pre_oos,
        "pre_oos_pass": pre_oos_pass,
        "base_full_recent": _window_metrics(base, "full_recent"),
        "stress_oos": stress_oos,
        "stress_full_recent": stress_recent,
        "extreme_full_recent": extreme_recent,
        "target": {
            "annualized_return": 1.0,
            "max_drawdown": MAX_TARGET_DRAWDOWN,
            "target_met": not reasons,
            "reasons": reasons,
        },
    }
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    selections.to_csv(
        output / "structural_specific_selection.csv", index=False
     )
    return report


def main() -> None:
    path = Path("runtime/structural_specific_daily_contracts.csv")
    if not path.exists():
        raise SystemExit("structural specific-contract history missing")
    report = evaluate(pd.read_csv(path))
    output = Path("runtime/structural_specific_report.json")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "pre_oos_pass": report["pre_oos_pass"],
                "selected_leverage": report["selected_leverage"],
                "stress_oos": report["stress_oos"],
                "stress_full_recent": report["stress_full_recent"],
                "extreme_full_recent": report["extreme_full_recent"],
                "target": report["target"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
