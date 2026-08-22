"""Roll-safe intraday validation for BU/FU and PP/V economic pairs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from math import log
from pathlib import Path

import numpy as np
import pandas as pd

START_MINUTE = 8 * 60 + 30
END_MINUTE = 15 * 60 + 10
MIN_DAYS_TO_DELIVERY = 20
STRESS_COST_BPS = 30.0
MAX_ACTIVE_PAIRS = 1
MAX_GROSS_LEVERAGE = 2.0
LEVERAGE_GRID = (0.5, 1.0, 1.5, 2.0)
MAX_CALIBRATION_DRAWDOWN = -0.15
MAX_TARGET_DRAWDOWN = -0.20

WINDOWS = {
    "prior1": ("2022-08-22", "2023-08-20"),
    "prior2": ("2023-08-21", "2024-08-20"),
    "train": ("2024-08-21", "2025-08-20"),
    "validation": ("2025-08-21", "2026-02-20"),
    "selection_full": ("2024-08-21", "2026-02-20"),
    "oos": ("2026-02-21", "2026-08-20"),
    "full_recent": ("2024-08-21", "2026-08-20"),
}
PAIRS = (("BU", "FU"), ("PP", "V"))


@dataclass(frozen=True)
class IntradayProfile:
    formation_bars: int
    entry_z: float
    min_correlation: float
    min_volatility_ratio: float
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_holding_bars: int = 8


PROFILES = tuple(
    IntradayProfile(formation, entry, correlation, volatility)
    for formation in (60, 120)
    for entry in (1.5, 2.0, 2.5)
    for correlation in (0.4, 0.6)
    for volatility in (0.7, 1.0)
)


def _metrics(intraday: pd.Series) -> dict:
    daily = intraday.groupby(intraday.index.normalize()).sum().sort_index()
    if daily.empty:
        return {
            "days": 0,
            "active_days": 0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "annualized_volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }
    equity = (1.0 + daily).cumprod()
    total = float(equity.iloc[-1] - 1.0)
    annualized = (
        (1.0 + total) ** (252.0 / len(daily)) - 1.0 if total > -1.0 else -1.0
    )
    standard_deviation = float(daily.std(ddof=1))
    sharpe = (
        float(daily.mean() / standard_deviation * np.sqrt(252.0))
        if standard_deviation > 0
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1.0
    return {
        "days": int(len(daily)),
        "active_days": int((daily != 0.0).sum()),
        "total_return": total,
        "annualized_return": float(annualized),
        "annualized_volatility": standard_deviation * np.sqrt(252.0),
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
    }


def _window_metrics(series: pd.Series, window: str) -> dict:
    start, end = WINDOWS[window]
    return _metrics(series.loc[pd.Timestamp(start):pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59)])


def _qualifies(item: dict) -> bool:
    return (
        item["active_days"] >= 5
        and item["annualized_return"] > 0.0
        and item["sharpe"] > 0.0
        and item["max_drawdown"] > -0.15
    )


def build_roll_safe_products(raw: pd.DataFrame):
    frame = raw.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    frame["delivery"] = pd.to_datetime(frame["delivery"], errors="coerce")
    for column in ("close", "volume", "hold"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=["datetime", "delivery", "product", "symbol", "close", "hold"]
    )
    minute = frame["datetime"].dt.hour * 60 + frame["datetime"].dt.minute
    frame = frame[(minute >= START_MINUTE) & (minute <= END_MINUTE)].copy()
    frame = frame[(frame["close"] > 0) & (frame["hold"] >= 0)]
    frame["date"] = frame["datetime"].dt.normalize()
    frame.drop_duplicates(["product", "symbol", "datetime"], keep="last", inplace=True)
    frame.sort_values(["product", "datetime", "symbol"], inplace=True)

    product_panels: dict[str, pd.DataFrame] = {}
    selection_rows: list[dict] = []
    quality: dict[str, dict] = {}
    for product in sorted({item for pair in PAIRS for item in pair}):
        product_frame = frame[frame["product"] == product].copy()
        if product_frame.empty:
            raise ValueError(f"60-minute data missing product: {product}")
        final_rows = (
            product_frame.sort_values("datetime")
            .groupby(["symbol", "date"], as_index=False)
            .tail(1)
        )
        final_by_date = {
            pd.Timestamp(day): rows.copy()
            for day, rows in final_rows.groupby("date")
        }
        dates = pd.DatetimeIndex(sorted(product_frame["date"].unique()))
        choices: dict[pd.Timestamp, str] = {}
        for index in range(1, len(dates)):
            trading_day = pd.Timestamp(dates[index])
            prior_day = pd.Timestamp(dates[index - 1])
            prior_rows = final_by_date.get(prior_day)
            if prior_rows is None or prior_rows.empty:
                continue
            eligible = prior_rows[
                (prior_rows["delivery"] - trading_day).dt.days >= MIN_DAYS_TO_DELIVERY
            ].copy()
            if eligible.empty:
                continue
            eligible["volume"] = eligible["volume"].fillna(0.0)
            eligible.sort_values(
                ["hold", "volume", "delivery", "symbol"],
                ascending=[False, False, True, True],
                inplace=True,
            )
            symbol = str(eligible.iloc[0]["symbol"])
            choices[trading_day] = symbol
            selection_rows.append(
                {"date": trading_day, "product": product, "symbol": symbol}
            )

        selected_parts = []
        for trading_day, symbol in choices.items():
            rows = product_frame[
                (product_frame["date"] == trading_day)
                & (product_frame["symbol"] == symbol)
            ].copy()
            if not rows.empty:
                selected_parts.append(rows)
        if not selected_parts:
            raise ValueError(f"no selected intraday bars for {product}")
        selected = pd.concat(selected_parts, ignore_index=True).sort_values("datetime")

        chain_value = 100.0
        chain_rows = []
        previous_symbol = None
        previous_close = None
        rolls = 0
        for row in selected.itertuples(index=False):
            current_symbol = str(row.symbol)
            current_close = float(row.close)
            if previous_symbol is not None and current_symbol != previous_symbol:
                rolls += 1
            if previous_symbol == current_symbol and previous_close and previous_close > 0:
                change = current_close / previous_close - 1.0
                if np.isfinite(change) and abs(change) <= 0.20:
                    chain_value *= 1.0 + change
            chain_rows.append(
                {
                    "datetime": pd.Timestamp(row.datetime),
                    "index": chain_value,
                    "symbol": current_symbol,
                }
            )
            previous_symbol = current_symbol
            previous_close = current_close
        panel = pd.DataFrame(chain_rows).drop_duplicates("datetime", keep="last").set_index("datetime")
        product_panels[product] = panel
        quality[product] = {
            "selected_days": int(selected["date"].nunique()),
            "contracts_used": int(selected["symbol"].nunique()),
            "rolls": int(rolls),
            "bars": int(len(panel)),
        }

    common = None
    for product in product_panels:
        product_index = product_panels[product].index
        common = product_index if common is None else common.intersection(product_index)
    common = pd.DatetimeIndex(common).sort_values()
    if len(common) < 1000:
        raise ValueError(f"too few exact common intraday timestamps: {len(common)}")

    close = pd.DataFrame(
        {product: panel.reindex(common)["index"] for product, panel in product_panels.items()},
        index=common,
    )
    returns = close.pct_change(fill_method=None)
    selections = pd.DataFrame(selection_rows).sort_values(["date", "product"])
    return close, returns, selections, quality


def _pair_statistics(close: pd.DataFrame, returns: pd.DataFrame, pair, profile):
    left_name, right_name = pair
    formation = profile.formation_bars
    left = np.log(close[left_name])
    right = np.log(close[right_name])
    right_mean = right.rolling(formation, min_periods=formation).mean().shift(1)
    left_mean = left.rolling(formation, min_periods=formation).mean().shift(1)
    covariance = right.rolling(formation, min_periods=formation).cov(left).shift(1)
    variance = right.rolling(formation, min_periods=formation).var().shift(1)
    beta = covariance / variance
    alpha = left_mean - beta * right_mean
    residual = left - (alpha + beta * right)
    minimum = max(20, formation // 2)
    residual_std = residual.rolling(formation, min_periods=minimum).std().shift(1)
    zscore = residual / residual_std
    correlation = right.rolling(formation, min_periods=formation).corr(left).shift(1)
    phi = residual.rolling(formation, min_periods=minimum).corr(residual.shift(1)).shift(1)
    half_life = pd.Series(999.0, index=close.index)
    valid = (phi > 0.0) & (phi < 0.9999)
    half_life.loc[valid] = -log(2.0) / np.log(phi.loc[valid])
    normalized = (returns[left_name] - beta * returns[right_name]) / (1.0 + beta.abs())
    fast_volatility = normalized.rolling(20, min_periods=20).std().shift(1)
    formation_volatility = normalized.rolling(formation, min_periods=minimum).std().shift(1)
    volatility_ratio = fast_volatility / formation_volatility
    return beta, zscore, correlation, half_life, volatility_ratio


def _desired_path(close, returns, pair, profile, statistics):
    left_name, right_name = pair
    beta, zscore, correlation, half_life, volatility_ratio = statistics
    index = close.index
    left_returns = returns[left_name].to_numpy(float)
    right_returns = returns[right_name].to_numpy(float)
    beta_values = beta.to_numpy(float)
    z_values = zscore.to_numpy(float)
    correlation_values = correlation.to_numpy(float)
    half_life_values = half_life.to_numpy(float)
    volatility_values = volatility_ratio.to_numpy(float)

    direction = np.zeros(len(index), dtype=int)
    score = np.zeros(len(index), dtype=float)
    forward_return = np.zeros(len(index), dtype=float)
    state = 0
    entry_index = -1
    left_weight = 0.0
    right_weight = 0.0

    for position_index in range(len(index) - 1):
        same_day_next = index[position_index].date() == index[position_index + 1].date()
        current_z = z_values[position_index]
        if state != 0:
            holding = position_index - entry_index
            if (
                not same_day_next
                or not np.isfinite(current_z)
                or abs(current_z) <= profile.exit_z
                or abs(current_z) >= profile.stop_z
                or holding >= profile.max_holding_bars
            ):
                state = 0
                entry_index = -1
                left_weight = 0.0
                right_weight = 0.0

        if state == 0 and same_day_next:
            eligible = (
                np.isfinite(current_z)
                and np.isfinite(beta_values[position_index])
                and beta_values[position_index] > 0.0
                and np.isfinite(correlation_values[position_index])
                and correlation_values[position_index] >= profile.min_correlation
                and np.isfinite(half_life_values[position_index])
                and 2.0 <= half_life_values[position_index] <= 60.0
                and np.isfinite(volatility_values[position_index])
                and volatility_values[position_index] >= profile.min_volatility_ratio
            )
            if eligible and current_z >= profile.entry_z:
                state = -1
            elif eligible and current_z <= -profile.entry_z:
                state = 1
            if state:
                entry_index = position_index
                current_beta = float(beta_values[position_index])
                normalization = 1.0 + abs(current_beta)
                left_weight = state / normalization
                right_weight = state * (-current_beta / normalization)

        direction[position_index] = state
        if state:
            score[position_index] = abs(current_z)
            next_index = position_index + 1
            left_value = left_returns[next_index]
            right_value = right_returns[next_index]
            if same_day_next and np.isfinite(left_value) and np.isfinite(right_value):
                forward_return[next_index] = (
                    left_weight * left_value + right_weight * right_value
                )
    return {"direction": direction, "score": score, "forward_return": forward_return}


def _portfolio(close, returns, profile, cost_bps=STRESS_COST_BPS):
    paths = {
        pair: _desired_path(
            close,
            returns,
            pair,
            profile,
            _pair_statistics(close, returns, pair, profile),
        )
        for pair in PAIRS
    }
    previous = {pair: 0.0 for pair in PAIRS}
    pnl = np.zeros(len(close.index), dtype=float)
    for position_index in range(len(close.index) - 1):
        candidates = [
            (pair, paths[pair]["score"][position_index])
            for pair in PAIRS
            if paths[pair]["direction"][position_index] != 0
        ]
        selected = sorted(candidates, key=lambda item: (-item[1], item[0]))[:MAX_ACTIVE_PAIRS]
        allocation = {pair: 0.0 for pair in PAIRS}
        for pair, _ in selected:
            allocation[pair] = float(paths[pair]["direction"][position_index])
        turnover = sum(abs(allocation[pair] - previous[pair]) for pair in PAIRS)
        pnl[position_index] -= turnover * cost_bps / 10000.0
        next_index = position_index + 1
        for pair, _ in selected:
            pnl[next_index] += paths[pair]["forward_return"][next_index]
        previous = allocation
    pnl[-1] -= sum(abs(value) for value in previous.values()) * cost_bps / 10000.0
    return pd.Series(pnl, index=close.index)


def _choose_leverage(calibration: pd.Series) -> float:
    selected = 0.0
    for leverage in LEVERAGE_GRID:
        item = _metrics(calibration * leverage)
        if (
            item["annualized_return"] > 0.0
            and item["max_drawdown"] > MAX_CALIBRATION_DRAWDOWN
            and bool((calibration * leverage > -1.0).all())
        ):
            selected = leverage
    return selected


def evaluate(raw: pd.DataFrame) -> dict:
    close, returns, selections, quality = build_roll_safe_products(raw)
    results = []
    portfolio_cache = {}
    for profile in PROFILES:
        series = _portfolio(close, returns, profile)
        portfolio_cache[profile] = series
        row = {
            "profile": asdict(profile),
            **{window: _window_metrics(series, window) for window in WINDOWS},
        }
        pre_oos = [row[name] for name in ("prior1", "prior2", "train", "validation")]
        row["pre_oos_pass"] = all(_qualifies(item) for item in pre_oos)
        row["pre_oos_score"] = (
            min(item["sharpe"] for item in pre_oos) if row["pre_oos_pass"] else -999.0
        )
        results.append(row)

    eligible = [item for item in results if item["pre_oos_pass"]]
    selected = max(eligible, key=lambda item: item["pre_oos_score"]) if eligible else None
    report = {
        "source": "AKShare/Sina concrete futures 60-minute bars",
        "role": "L4 intraday roll-safe signal evidence; no historical L1/depth",
        "historical_l1_available": False,
        "specific_contracts": True,
        "roll_safe": True,
        "pristine_final_oos": True,
        "day_session_only": True,
        "stress_cost_bps_one_way": STRESS_COST_BPS,
        "max_active_pairs": MAX_ACTIVE_PAIRS,
        "max_gross_leverage": MAX_GROSS_LEVERAGE,
        "profile_count": len(PROFILES),
        "common_bars": int(len(close)),
        "data_quality": quality,
        "profiles": results,
        "support": {
            "eligible_profiles": len(eligible),
            "formation_60": sum(
                item["pre_oos_pass"] and item["profile"]["formation_bars"] == 60
                for item in results
            ),
            "formation_120": sum(
                item["pre_oos_pass"] and item["profile"]["formation_bars"] == 120
                for item in results
            ),
        },
        "selected_profile": selected["profile"] if selected else None,
        "selected_oos_unlevered": selected["oos"] if selected else None,
        "selected_full_recent_unlevered": selected["full_recent"] if selected else None,
    }
    reasons: list[str] = []
    alpha_reasons: list[str] = []
    if selected is None:
        report["selected_leverage"] = 0.0
        report["selected_oos"] = None
        report["selected_full_recent"] = None
        alpha_reasons.append("no intraday profile survives all pre-OOS gates")
    else:
        profile = IntradayProfile(**selected["profile"])
        series = portfolio_cache[profile]
        start, end = WINDOWS["selection_full"]
        calibration = series.loc[pd.Timestamp(start):pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59)]
        leverage = _choose_leverage(calibration)
        report["selected_leverage"] = leverage
        scaled = series * leverage if leverage > 0 else series * 0.0
        report["selected_oos"] = _window_metrics(scaled, "oos")
        report["selected_full_recent"] = _window_metrics(scaled, "full_recent")
        if len(eligible) < 2:
            alpha_reasons.append("intraday profile neighborhood support is below two")
        if report["selected_oos"]["annualized_return"] <= 0:
            alpha_reasons.append("intraday final OOS return is not positive")
        if report["selected_full_recent"]["annualized_return"] <= 0:
            alpha_reasons.append("intraday recent two-year return is not positive")
        if report["selected_full_recent"]["max_drawdown"] <= MAX_TARGET_DRAWDOWN:
            alpha_reasons.append("intraday drawdown exceeds 20%")
        if leverage <= 0:
            alpha_reasons.append("no leverage level satisfies intraday calibration drawdown gate")

    report["alpha_survives_intraday"] = not alpha_reasons
    report["alpha_reasons"] = alpha_reasons
    reasons.extend(alpha_reasons)
    if report.get("selected_full_recent") is None or report["selected_full_recent"]["annualized_return"] < 1.0:
        reasons.append("stressed intraday two-year annualized return is below 100%")
    report["target"] = {
        "annualized_return": 1.0,
        "max_drawdown": MAX_TARGET_DRAWDOWN,
        "target_met": not reasons,
        "reasons": reasons,
    }

    output = Path("runtime")
    selections.to_csv(output / "specific_pair_60m_selection.csv", index=False)
    return report


def main() -> None:
    path = Path("runtime/specific_pair_60m_contracts.csv")
    if not path.exists():
        raise SystemExit("specific 60-minute history missing")
    report = evaluate(pd.read_csv(path))
    output = Path("runtime/specific_pair_intraday_report.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "common_bars": report["common_bars"],
        "data_quality": report["data_quality"],
        "support": report["support"],
        "selected_profile": report["selected_profile"],
        "selected_leverage": report["selected_leverage"],
        "selected_oos": report["selected_oos"],
        "selected_full_recent": report["selected_full_recent"],
        "alpha_survives_intraday": report["alpha_survives_intraday"],
        "alpha_reasons": report["alpha_reasons"],
        "target": report["target"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
