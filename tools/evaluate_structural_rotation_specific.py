"""Roll-safe L4 validation for the structural relative-value rotation.

The strategy set is frozen from pre-OOS L3 evidence:
- steel mill margin: RB - 1.6 * I - 0.5 * J
- coke margin: J - 1.3 * JM
- soybean relative residual: log(Y) ~ log(A) + log(M)
- fuel pair: BU/FU rolling residual

At most one structure is active. Candidate ranking uses the frozen pre-OOS quality
weights and current causal z-score only. Contract selection uses concrete futures and
return t->t+1 is always earned on the same contract selected at close t.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from math import log
from pathlib import Path

import numpy as np
import pandas as pd

WINDOWS = {
    "prior1": ("2022-08-22", "2023-08-20"),
    "prior2": ("2023-08-21", "2024-08-20"),
    "train": ("2024-08-21", "2025-08-20"),
    "validation": ("2025-08-21", "2026-02-20"),
    "selection_full": ("2024-08-21", "2026-02-20"),
    "oos": ("2026-02-21", "2026-08-20"),
    "full_recent": ("2024-08-21", "2026-08-20"),
}
MIN_DAYS_TO_DELIVERY = 20
MIN_PRODUCT_DAYS = 700
BASE_COST_BPS = 5.0
STRESS_COST_BPS = 10.0
EXTREME_COST_BPS = 30.0
MAX_GROSS_LEVERAGE = 4.2
LEVERAGE_GRID = (1.0, 2.0, 3.0, 3.5, 4.0, 4.1, 4.2)
MAX_CALIBRATION_DRAWDOWN = -0.20
MAX_TARGET_DRAWDOWN = -0.20
MAX_MARGIN_RATIO_PROXY = 0.85
MARGIN_PROXY = {"steel": 0.16, "coke": 0.20, "soy": 0.12, "bufu": 0.12}
QUALITY_WEIGHT = {
    "steel": 0.8864327409412001,
    "coke": 0.39677352527489423,
    "soy": 0.16344248976709766,
    "bufu": 0.6269987719257136,
}
REQUIRED_PRODUCTS = ("A", "BU", "FU", "I", "J", "JM", "M", "RB", "Y")


@dataclass(frozen=True)
class MeanReversionProfile:
    formation: int
    entry_z: float
    min_volatility_ratio: float
    max_holding_days: int
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_half_life: float = 60.0


STEEL_PROFILE = MeanReversionProfile(60, 2.5, 1.0, 20)
COKE_PROFILE = MeanReversionProfile(60, 1.5, 0.7, 20)
SOY_PROFILE = MeanReversionProfile(120, 1.5, 0.7, 60)
BUFU_PROFILE = MeanReversionProfile(60, 2.0, 0.7, 60)


def _metrics(series: pd.Series) -> dict:
    values = series.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if values.empty:
        return {"days": 0, "active_days": 0, "total_return": 0.0, "annualized_return": 0.0, "annualized_volatility": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    equity = (1.0 + values).cumprod()
    total = float(equity.iloc[-1] - 1.0)
    annualized = (1.0 + total) ** (252.0 / len(values)) - 1.0 if total > -1.0 else -1.0
    std = float(values.std(ddof=1))
    sharpe = float(values.mean() / std * np.sqrt(252.0)) if std > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    return {"days": int(len(values)), "active_days": int((values != 0.0).sum()), "total_return": total, "annualized_return": float(annualized), "annualized_volatility": std * np.sqrt(252.0), "sharpe": sharpe, "max_drawdown": float(drawdown.min())}


def _window_metrics(series: pd.Series, name: str) -> dict:
    start, end = WINDOWS[name]
    return _metrics(series.loc[pd.Timestamp(start):pd.Timestamp(end)])


def _qualifies(item: dict, minimum_active_days: int = 5) -> bool:
    return item["active_days"] >= minimum_active_days and item["annualized_return"] > 0.0 and item["sharpe"] > 0.0 and item["max_drawdown"] > MAX_TARGET_DRAWDOWN


def build_roll_safe_panel(raw: pd.DataFrame):
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["delivery"] = pd.to_datetime(frame["delivery"], errors="coerce")
    for column in ("close", "volume", "hold"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "delivery", "product", "symbol", "close", "hold"])
    frame = frame[(frame["close"] > 0) & (frame["hold"] >= 0)]
    frame.drop_duplicates(["product", "symbol", "date"], keep="last", inplace=True)
    frame.sort_values(["product", "date", "symbol"], inplace=True)
    index_series: dict[str, pd.Series] = {}
    return_series: dict[str, pd.Series] = {}
    selected_rows: list[dict] = []
    quality: dict[str, dict] = {}
    for product in REQUIRED_PRODUCTS:
        product_frame = frame[frame["product"] == product].copy()
        if product_frame.empty:
            raise ValueError(f"structural specific-contract data missing product: {product}")
        by_symbol = {str(symbol): group.set_index("date").sort_index() for symbol, group in product_frame.groupby("symbol")}
        dates = pd.DatetimeIndex(sorted(product_frame["date"].unique()))
        choices: dict[pd.Timestamp, str] = {}
        for trading_day, day_rows in product_frame.groupby("date"):
            trading_day = pd.Timestamp(trading_day)
            eligible = day_rows[(day_rows["delivery"] - trading_day).dt.days >= MIN_DAYS_TO_DELIVERY].copy()
            if eligible.empty:
                continue
            eligible["volume"] = eligible["volume"].fillna(0.0)
            eligible.sort_values(["hold", "volume", "delivery", "symbol"], ascending=[False, False, True, True], inplace=True)
            chosen = eligible.iloc[0]
            choices[trading_day] = str(chosen["symbol"])
            selected_rows.append({"date": trading_day, "product": product, "symbol": str(chosen["symbol"]), "delivery": pd.Timestamp(chosen["delivery"]), "close": float(chosen["close"]), "open_interest": float(chosen["hold"]), "days_to_delivery": int((pd.Timestamp(chosen["delivery"]) - trading_day).days)})
        selected = pd.Series(choices, dtype=object).sort_index()
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
                if symbol_frame is not None and previous_date in symbol_frame.index and trading_day in symbol_frame.index:
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
            if previous_symbol is not None and current_symbol is not None and current_symbol != previous_symbol:
                rolls += 1
            previous_symbol = current_symbol
            previous_date = trading_day
        valid_days = int(selected.index.nunique())
        if valid_days < MIN_PRODUCT_DAYS:
            raise ValueError(f"structural specific-contract coverage too short for {product}: {valid_days}")
        index_series[product] = synthetic
        return_series[product] = tradable
        quality[product] = {"selected_days": valid_days, "contracts_used": int(selected.nunique()), "rolls": int(rolls), "missing_next_contract_returns": int(missing_next), "missing_next_ratio": float(missing_next / max(len(all_dates) - 1, 1))}
    close = pd.DataFrame(index_series).sort_index()
    returns = pd.DataFrame(return_series).reindex(close.index)
    selections = pd.DataFrame(selected_rows).sort_values(["date", "product"])
    if int(selections["days_to_delivery"].min()) < MIN_DAYS_TO_DELIVERY:
        raise AssertionError("structural delivery blackout violated")
    return close, returns, selections, quality


def _half_life(values: np.ndarray) -> float:
    if len(values) < 3:
        return 999.0
    left = values[:-1]
    right = values[1:]
    if np.std(left) <= 1e-12 or np.std(right) <= 1e-12:
        return 999.0
    phi = float(np.corrcoef(left, right)[0, 1])
    if not 0.0 < phi < 0.9999:
        return 999.0
    return float(-log(2.0) / np.log(phi))


def _physical_path(close: pd.DataFrame, returns: pd.DataFrame, coefficients: dict[str, float], profile: MeanReversionProfile) -> pd.DataFrame:
    index = close.index
    names = list(coefficients)
    coefficient = np.asarray([coefficients[name] for name in names], float)
    prices = close[names].to_numpy(float)
    leg_returns = returns[names].to_numpy(float)
    spread = pd.Series(prices @ coefficient, index=index)
    reference_mean = spread.rolling(profile.formation, min_periods=profile.formation).mean().shift(1)
    reference_std = spread.rolling(profile.formation, min_periods=profile.formation).std(ddof=0).shift(1)
    zscore = (spread - reference_mean) / reference_std
    gross_notional = np.abs(prices * coefficient).sum(axis=1)
    normalized_weights = (prices * coefficient) / gross_notional[:, None]
    spread_return = pd.Series(np.nansum(normalized_weights * leg_returns, axis=1), index=index)
    fast_vol = spread_return.rolling(20, min_periods=20).std().shift(1)
    slow_vol = spread_return.rolling(profile.formation, min_periods=max(20, profile.formation // 2)).std().shift(1)
    volatility_ratio = fast_vol / slow_vol
    direction = np.zeros(len(index), dtype=int)
    score = np.zeros(len(index), dtype=float)
    next_return = np.zeros(len(index), dtype=float)
    state = 0
    entry_index = -1
    weights: np.ndarray | None = None
    spread_values = spread.to_numpy(float)
    z_values = zscore.to_numpy(float)
    volatility_values = volatility_ratio.to_numpy(float)
    for position_index in range(len(index) - 1):
        current_z = z_values[position_index]
        if state == 0:
            start = max(0, position_index - profile.formation)
            history = spread_values[start:position_index]
            half_life = _half_life(history[np.isfinite(history)])
            eligible = np.isfinite(current_z) and 2.0 <= half_life <= profile.max_half_life and np.isfinite(volatility_values[position_index]) and volatility_values[position_index] >= profile.min_volatility_ratio
            state = -1 if eligible and current_z >= profile.entry_z else (1 if eligible and current_z <= -profile.entry_z else 0)
            if state:
                entry_index = position_index
                raw = state * coefficient * prices[position_index]
                weights = raw / np.abs(raw).sum()
        else:
            holding = position_index - entry_index
            if not np.isfinite(current_z) or abs(current_z) <= profile.exit_z or abs(current_z) >= profile.stop_z or holding >= profile.max_holding_days:
                state = 0
                entry_index = -1
                weights = None
        if state and weights is not None:
            direction[position_index] = state
            score[position_index] = abs(current_z)
            next_leg_returns = leg_returns[position_index + 1]
            if np.all(np.isfinite(next_leg_returns)):
                next_return[position_index + 1] = float(weights @ next_leg_returns)
    return pd.DataFrame({"direction": direction, "score": score, "next_return": next_return}, index=index)


def _soy_path(close: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    index = close.index
    y = np.log(close["Y"].to_numpy(float))
    x = np.column_stack([np.log(close["A"].to_numpy(float)), np.log(close["M"].to_numpy(float))])
    y_return = returns["Y"].to_numpy(float)
    x_returns = np.column_stack([returns["A"].to_numpy(float), returns["M"].to_numpy(float)])
    n = len(index)
    beta = np.full((n, 2), np.nan)
    zscore = np.full(n, np.nan)
    r_squared = np.full(n, np.nan)
    half_life = np.full(n, np.nan)
    volatility_ratio = np.full(n, np.nan)
    for position_index in range(SOY_PROFILE.formation, n):
        ys = y[position_index - SOY_PROFILE.formation:position_index]
        xs = x[position_index - SOY_PROFILE.formation:position_index]
        mask = np.isfinite(ys) & np.all(np.isfinite(xs), axis=1)
        if int(mask.sum()) < 60:
            continue
        yy = ys[mask]
        xx = xs[mask]
        design = np.column_stack([np.ones(len(xx)), xx])
        coefficients = np.linalg.lstsq(design, yy, rcond=None)[0]
        residual = yy - design @ coefficients
        residual_std = float(np.std(residual, ddof=0))
        if residual_std <= 1e-12:
            continue
        total = float(np.sum((yy - yy.mean()) ** 2))
        residual_sum = float(np.sum(residual ** 2))
        r_squared[position_index] = 1.0 - residual_sum / total if total > 1e-12 else np.nan
        beta[position_index] = coefficients[1:]
        current_x = x[position_index]
        if np.all(np.isfinite(current_x)) and np.isfinite(y[position_index]):
            current_residual = y[position_index] - (coefficients[0] + current_x @ coefficients[1:])
            zscore[position_index] = current_residual / residual_std
        half_life[position_index] = _half_life(residual)
        delta = np.diff(residual)
        if len(delta) >= 20:
            fast = float(np.std(delta[-20:], ddof=1))
            slow = float(np.std(delta, ddof=1))
            volatility_ratio[position_index] = fast / slow if slow > 1e-12 else np.nan
    direction = np.zeros(n, dtype=int)
    score = np.zeros(n, dtype=float)
    next_return = np.zeros(n, dtype=float)
    state = 0
    entry_index = -1
    weights: np.ndarray | None = None
    for position_index in range(n - 1):
        current_z = zscore[position_index]
        if state == 0:
            current_beta = beta[position_index]
            eligible = np.isfinite(current_z) and np.all(np.isfinite(current_beta)) and np.all(current_beta > 0.0) and np.isfinite(r_squared[position_index]) and r_squared[position_index] >= 0.4 and np.isfinite(half_life[position_index]) and 2.0 <= half_life[position_index] <= 60.0 and np.isfinite(volatility_ratio[position_index]) and volatility_ratio[position_index] >= SOY_PROFILE.min_volatility_ratio
            state = -1 if eligible and current_z >= SOY_PROFILE.entry_z else (1 if eligible and current_z <= -SOY_PROFILE.entry_z else 0)
            if state:
                entry_index = position_index
                raw = state * np.r_[1.0, -current_beta]
                weights = raw / np.abs(raw).sum()
        else:
            holding = position_index - entry_index
            if not np.isfinite(current_z) or abs(current_z) <= SOY_PROFILE.exit_z or abs(current_z) >= SOY_PROFILE.stop_z or holding >= SOY_PROFILE.max_holding_days:
                state = 0
                entry_index = -1
                weights = None
        if state and weights is not None:
            direction[position_index] = state
            score[position_index] = abs(current_z)
            next_returns = np.r_[y_return[position_index + 1], x_returns[position_index + 1]]
            if np.all(np.isfinite(next_returns)):
                next_return[position_index + 1] = float(weights @ next_returns)
    return pd.DataFrame({"direction": direction, "score": score, "next_return": next_return}, index=index)


def _bufu_path(close: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    formation = BUFU_PROFILE.formation
    left = np.log(close["BU"])
    right = np.log(close["FU"])
    right_mean = right.rolling(formation, min_periods=formation).mean().shift(1)
    left_mean = left.rolling(formation, min_periods=formation).mean().shift(1)
    covariance = right.rolling(formation, min_periods=formation).cov(left).shift(1)
    variance = right.rolling(formation, min_periods=formation).var().shift(1)
    beta = covariance / variance
    alpha = left_mean - beta * right_mean
    residual = left - (alpha + beta * right)
    residual_std = residual.rolling(formation, min_periods=30).std().shift(1)
    zscore = residual / residual_std
    correlation = right.rolling(formation, min_periods=formation).corr(left).shift(1)
    phi = residual.rolling(formation, min_periods=30).corr(residual.shift(1)).shift(1)
    half_life = pd.Series(999.0, index=close.index)
    valid = (phi > 0.0) & (phi < 0.9999)
    half_life.loc[valid] = -log(2.0) / np.log(phi.loc[valid])
    normalized = (returns["BU"] - beta * returns["FU"]) / (1.0 + beta.abs())
    fast_vol = normalized.rolling(20, min_periods=20).std().shift(1)
    slow_vol = normalized.rolling(formation, min_periods=30).std().shift(1)
    volatility_ratio = fast_vol / slow_vol
    index = close.index
    left_returns = returns["BU"].to_numpy(float)
    right_returns = returns["FU"].to_numpy(float)
    direction = np.zeros(len(index), dtype=int)
    score = np.zeros(len(index), dtype=float)
    next_return = np.zeros(len(index), dtype=float)
    state = 0
    entry_index = -1
    left_weight = right_weight = 0.0
    for position_index in range(len(index) - 1):
        current_z = zscore.iat[position_index]
        if state == 0:
            eligible = np.isfinite(current_z) and np.isfinite(beta.iat[position_index]) and beta.iat[position_index] > 0.0 and np.isfinite(correlation.iat[position_index]) and correlation.iat[position_index] >= 0.4 and np.isfinite(half_life.iat[position_index]) and 2.0 <= half_life.iat[position_index] <= 60.0 and np.isfinite(volatility_ratio.iat[position_index]) and volatility_ratio.iat[position_index] >= BUFU_PROFILE.min_volatility_ratio
            state = -1 if eligible and current_z >= BUFU_PROFILE.entry_z else (1 if eligible and current_z <= -BUFU_PROFILE.entry_z else 0)
            if state:
                entry_index = position_index
                current_beta = float(beta.iat[position_index])
                normalization = 1.0 + abs(current_beta)
                left_weight = state / normalization
                right_weight = state * (-current_beta / normalization)
        else:
            holding = position_index - entry_index
            if not np.isfinite(current_z) or abs(current_z) <= BUFU_PROFILE.exit_z or abs(current_z) >= BUFU_PROFILE.stop_z or holding >= BUFU_PROFILE.max_holding_days:
                state = 0
                entry_index = -1
                left_weight = right_weight = 0.0
        if state:
            direction[position_index] = state
            score[position_index] = abs(current_z)
            if np.isfinite(left_returns[position_index + 1]) and np.isfinite(right_returns[position_index + 1]):
                next_return[position_index + 1] = left_weight * left_returns[position_index + 1] + right_weight * right_returns[position_index + 1]
    return pd.DataFrame({"direction": direction, "score": score, "next_return": next_return}, index=index)


def build_paths(close: pd.DataFrame, returns: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "steel": _physical_path(close, returns, {"RB": 1.0, "I": -1.6, "J": -0.5}, STEEL_PROFILE),
        "coke": _physical_path(close, returns, {"J": 1.0, "JM": -1.3}, COKE_PROFILE),
        "soy": _soy_path(close, returns),
        "bufu": _bufu_path(close, returns),
    }


def _selected_candidate(paths: dict[str, pd.DataFrame], position_index: int):
    candidates: list[tuple[float, str, int]] = []
    for name, path in paths.items():
        direction = int(path["direction"].iat[position_index])
        if direction == 0:
            continue
        score = float(path["score"].iat[position_index]) * QUALITY_WEIGHT[name]
        candidates.append((-score, name, direction))
    if not candidates:
        return None
    candidates.sort()
    _, name, direction = candidates[0]
    return name, direction


def _rotation(paths: dict[str, pd.DataFrame], *, cost_bps: float, leverage: float) -> pd.Series:
    index = next(iter(paths.values())).index
    pnl = np.zeros(len(index), dtype=float)
    previous = None
    for position_index in range(len(index) - 1):
        current = _selected_candidate(paths, position_index)
        if current != previous:
            if previous is None and current is not None:
                turnover = leverage
            elif previous is not None and current is None:
                turnover = leverage
            elif previous is not None and current is not None:
                turnover = 2.0 * leverage
            else:
                turnover = 0.0
            pnl[position_index] -= turnover * cost_bps / 10000.0
        if current is not None:
            pnl[position_index + 1] += leverage * paths[current[0]]["next_return"].iat[position_index + 1]
        previous = current
    if previous is not None:
        pnl[-1] -= leverage * cost_bps / 10000.0
    return pd.Series(pnl, index=index)


def _choose_leverage(paths: dict[str, pd.DataFrame]) -> float:
    selected = 0.0
    start, end = WINDOWS["selection_full"]
    for leverage in LEVERAGE_GRID:
        if leverage > MAX_GROSS_LEVERAGE:
            continue
        if any(leverage * margin > MAX_MARGIN_RATIO_PROXY for margin in MARGIN_PROXY.values()):
            continue
        stressed = _rotation(paths, cost_bps=STRESS_COST_BPS, leverage=leverage)
        calibration = stressed.loc[pd.Timestamp(start):pd.Timestamp(end)]
        item = _metrics(calibration)
        if item["annualized_return"] > 0.0 and item["max_drawdown"] > MAX_CALIBRATION_DRAWDOWN and bool((calibration > -1.0).all()):
            selected = leverage
    return selected


def evaluate(raw: pd.DataFrame) -> dict:
    close, returns, selections, data_quality = build_roll_safe_panel(raw)
    paths = build_paths(close, returns)
    stress_unlevered = _rotation(paths, cost_bps=STRESS_COST_BPS, leverage=1.0)
    pre_oos = {name: _window_metrics(stress_unlevered, name) for name in ("prior1", "prior2", "train", "validation")}
    pre_oos_pass = all(_qualifies(item) for item in pre_oos.values())
    leverage = _choose_leverage(paths) if pre_oos_pass else 0.0
    base = _rotation(paths, cost_bps=BASE_COST_BPS, leverage=leverage) if leverage else stress_unlevered * 0.0
    stress = _rotation(paths, cost_bps=STRESS_COST_BPS, leverage=leverage) if leverage else stress_unlevered * 0.0
    extreme = _rotation(paths, cost_bps=EXTREME_COST_BPS, leverage=leverage) if leverage else stress_unlevered * 0.0
    stress_oos = _window_metrics(stress, "oos")
    stress_recent = _window_metrics(stress, "full_recent")
    extreme_recent = _window_metrics(extreme, "full_recent")
    reasons: list[str] = []
    if not pre_oos_pass:
        reasons.append("specific-contract structural rotation fails a pre-OOS gate")
    if leverage <= 0.0:
        reasons.append("no leverage level satisfies pre-OOS drawdown and margin gates")
    if stress_oos["annualized_return"] <= 0.0:
        reasons.append("stress-cost Final OOS return is not positive")
    if stress_recent["annualized_return"] < 1.0:
        reasons.append("stress-cost roll-safe two-year annualized return is below 100%")
    if stress_recent["max_drawdown"] <= MAX_TARGET_DRAWDOWN:
        reasons.append("stress-cost roll-safe two-year drawdown exceeds 20%")
    if extreme_recent["annualized_return"] <= 0.0:
        reasons.append("30bp extreme-cost two-year return is not positive")
    if extreme_recent["max_drawdown"] <= -0.30:
        reasons.append("30bp extreme-cost drawdown exceeds 30%")
    report = {
        "source": "AKShare/Sina concrete futures daily bars",
        "role": "L4 roll-safe structural rotation evidence; historical L1/depth unavailable",
        "specific_contracts": True,
        "roll_safe": True,
        "historical_l1_available": False,
        "pristine_final_oos": False,
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
        "target": {"annualized_return": 1.0, "max_drawdown": MAX_TARGET_DRAWDOWN, "target_met": not reasons, "reasons": reasons},
    }
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    selections.to_csv(output / "structural_specific_selection.csv", index=False)
    return report


def main() -> None:
    path = Path("runtime/structural_specific_daily_contracts.csv")
    if not path.exists():
        raise SystemExit("structural specific-contract history missing")
    report = evaluate(pd.read_csv(path))
    output = Path("runtime/structural_specific_report.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"pre_oos_pass": report["pre_oos_pass"], "selected_leverage": report["selected_leverage"], "stress_oos": report["stress_oos"], "stress_full_recent": report["stress_full_recent"], "extreme_full_recent": report["extreme_full_recent"], "target": report["target"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
