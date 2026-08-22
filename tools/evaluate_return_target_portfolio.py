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


WINDOWS = {
    "prior1": ("2022-08-22", "2023-08-20"),
    "prior2": ("2023-08-21", "2024-08-20"),
    "train": ("2024-08-21", "2025-08-20"),
    "validation": ("2025-08-21", "2026-02-20"),
    "selection_full": ("2024-08-21", "2026-02-20"),
    "oos": ("2026-02-21", "2026-08-20"),
    "full_recent": ("2024-08-21", "2026-08-20"),
}
BASE_COST_BPS = 5.0
STRESS_COST_BPS = 15.0
EXTREME_COST_BPS = 30.0
MAX_GROSS_LEVERAGE = 2.0
MAX_ABS_DAILY_RETURN = 0.20
META_POOL_SIZE = 16

EXCHANGE_PRODUCTS = {
    "DCE": {
        "A", "B", "C", "CS", "EB", "EG", "I", "J", "JM", "L", "LH",
        "M", "P", "PG", "PP", "V", "Y",
    },
    "CZCE": {
        "AP", "CF", "CJ", "FG", "MA", "OI", "PF", "PK", "RM", "SA",
        "SF", "SM", "SR", "TA", "UR",
    },
    "SHFE": {
        "AG", "AL", "AU", "BU", "CU", "FU", "HC", "NI", "PB", "RB",
        "RU", "SN", "SP", "SS", "ZN",
    },
    "INE": {"BC", "LU", "NR"},
}
PRODUCT_EXCHANGE = {
    product: exchange
    for exchange, products in EXCHANGE_PRODUCTS.items()
    for product in products
}


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
    """Return the bounded first-wave search space; leverage never exceeds 2x."""
    rows: list[AlphaTemplate] = []
    for slow in (10, 20, 40, 60, 120):
        for rebalance in (1, 5):
            for gross in (1.0, 1.5, 2.0):
                rows.append(
                    AlphaTemplate("momentum", slow, 0, 20, rebalance, 1, gross)
                )
    for fast in (1, 3, 5, 10):
        for rebalance in (1, 2, 5):
            for gross in (1.0, 1.5):
                rows.append(
                    AlphaTemplate("reversal", 0, fast, 20, rebalance, 1, gross)
                )
    for slow, fast in ((20, 3), (40, 5), (60, 5), (60, 10), (120, 10)):
        for rebalance in (1, 5):
            for gross in (1.0, 1.5, 2.0):
                rows.append(
                    AlphaTemplate("slow_fast", slow, fast, 20, rebalance, 1, gross)
                )
    for slow in (20, 40, 60):
        for rebalance in (1, 5):
            for gross in (1.0, 1.5, 2.0):
                rows.append(
                    AlphaTemplate("breakout", slow, 5, 20, rebalance, 1, gross)
                )
    return tuple(rows)


def template_id(template: AlphaTemplate) -> str:
    return (
        f"{template.family}_s{template.slow}_f{template.fast}_v{template.vol_window}"
        f"_r{template.rebalance}_p{template.max_pairs}_g{template.gross_leverage:g}"
    )


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
            str(name)
            for name in available.index
            if exchange_map.get(str(name)) == exchange
        ]
        if len(names) < 2:
            continue
        ranked = available.loc[names].sort_values("score", kind="stable")
        low_names = [str(name) for name in ranked.index]
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
            candidates.append((spread, long_name, short_name))

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

    pair_gross = min(gross_leverage, MAX_GROSS_LEVERAGE) / len(selected)
    legs: list[str] = []
    for _, long_name, short_name in selected:
        long_inv = 1.0 / float(available.loc[long_name, "volatility"])
        short_inv = 1.0 / float(available.loc[short_name, "volatility"])
        scale = pair_gross / (long_inv + short_inv)
        weights.loc[long_name] = long_inv * scale
        weights.loc[short_name] = -short_inv * scale
        legs.extend((long_name, short_name))

    gross = float(weights.abs().sum())
    if gross > MAX_GROSS_LEVERAGE + 1e-12:
        weights *= MAX_GROSS_LEVERAGE / gross
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
        turnover = 0.0
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
                "turnover": turnover,
                "legs": legs,
            }
        )
    return output, audit


def _metrics(series: pd.Series) -> dict:
    values = series.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if values.empty:
        return {
            "days": 0,
            "active_days": 0,
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
    std = float(values.std(ddof=1))
    sharpe = (
        float(values.mean() / std * np.sqrt(252.0))
        if std > 1e-12
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1.0
    return {
        "days": int(len(values)),
        "active_days": int((values.abs() > 1e-15).sum()),
        "total_return": total,
        "annualized_return": float(annualized),
        "annualized_volatility": float(std * np.sqrt(252.0)),
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
    }


def _window_metrics(series: pd.Series, name: str) -> dict:
    start, end = WINDOWS[name]
    return _metrics(series.loc[pd.Timestamp(start) : pd.Timestamp(end)])


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


def dynamic_rotate(
    streams: dict[str, pd.Series],
    *,
    meta_lookback: int,
    rebalance: int,
    count: int,
    switch_cost_bps: float,
) -> tuple[pd.Series, list[dict]]:
    """Rotate among template PnL streams using trailing history only."""
    if not streams:
        return pd.Series(dtype=float), []
    frame = pd.DataFrame(streams).sort_index().fillna(0.0)
    output = pd.Series(0.0, index=frame.index, dtype=float)
    selected: list[str] = []
    audit: list[dict] = []
    for index in range(len(frame)):
        if index >= meta_lookback and (
            not selected or index % max(rebalance, 1) == 0
        ):
            history = frame.iloc[index - meta_lookback : index]
            ranked: list[tuple[float, str]] = []
            for name in frame.columns:
                item = _metrics(history[name])
                if (
                    item["annualized_return"] <= 0.0
                    or item["max_drawdown"] <= -0.20
                ):
                    continue
                score = (
                    4.0 * item["annualized_return"]
                    + 0.5 * item["sharpe"]
                    - 2.0 * abs(item["max_drawdown"])
                )
                ranked.append((score, str(name)))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            next_selected = [name for _, name in ranked[:count]]
            if next_selected != selected:
                old = set(selected)
                new = set(next_selected)
                churn = len(old.symmetric_difference(new)) / max(count, 1)
                output.iloc[index] -= churn * switch_cost_bps / 10000.0
            selected = next_selected
        if selected:
            output.iloc[index] += float(frame.iloc[index][selected].mean())
        audit.append(
            {"date": frame.index[index], "selected": list(selected)}
        )
    return output, audit


def build_panel(
    raw: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["product"] = frame["product"].astype(str).str.upper()
    frame = frame.dropna(subset=["date", "product", "close"])
    frame = frame[frame["close"] > 0]
    frame.drop_duplicates(["date", "product"], keep="last", inplace=True)
    close = (
        frame.pivot(index="date", columns="product", values="close")
        .sort_index()
        .astype(float)
    )
    returns = close.pct_change(fill_method=None)
    outlier = returns.abs() > MAX_ABS_DAILY_RETURN
    removed = int(outlier.sum().sum())
    returns = returns.mask(outlier)
    exchanges = {
        str(product): PRODUCT_EXCHANGE[str(product)]
        for product in returns.columns
        if str(product) in PRODUCT_EXCHANGE
    }
    usable = [column for column in returns.columns if str(column) in exchanges]
    returns = returns[usable]
    coverage = {
        "products": int(len(usable)),
        "trading_days": int(len(returns.index)),
        "first": returns.index.min().date().isoformat() if len(returns) else None,
        "last": returns.index.max().date().isoformat() if len(returns) else None,
        "return_outliers_removed": removed,
    }
    return returns, exchanges, coverage


def _template_selection_score(row: dict) -> float:
    train = row["base"]["train"]
    validation = row["base"]["validation"]
    stress = row["stress"]["selection_full"]
    return (
        2.0 * train["annualized_return"]
        + 3.0 * validation["annualized_return"]
        + 0.5 * min(train["sharpe"], validation["sharpe"])
        + 0.5 * stress["annualized_return"]
        - 2.0 * abs(max(train["max_drawdown"], validation["max_drawdown"], key=abs))
    )


def evaluate(raw: pd.DataFrame) -> dict:
    returns, exchanges, coverage = build_panel(raw)
    if coverage["products"] < 2:
        raise ValueError("return-target research needs at least two known products")

    templates = primary_templates()
    base_streams: dict[str, pd.Series] = {}
    stress_streams: dict[str, pd.Series] = {}
    extreme_streams: dict[str, pd.Series] = {}
    template_lookup: dict[str, AlphaTemplate] = {}
    rows: list[dict] = []

    for template in templates:
        name = template_id(template)
        template_lookup[name] = template
        base, base_audit = simulate_template(
            returns, exchanges, template, cost_bps=BASE_COST_BPS
        )
        stress, _ = simulate_template(
            returns, exchanges, template, cost_bps=STRESS_COST_BPS
        )
        base_streams[name] = base
        stress_streams[name] = stress
        turnover = sum(float(item["turnover"]) for item in base_audit)
        row = {
            "template_id": name,
            "template": asdict(template),
            "annual_turnover": float(turnover * 252.0 / max(len(base), 1)),
            "base": {
                key: _window_metrics(base, key)
                for key in WINDOWS
            },
            "stress": {
                key: _window_metrics(stress, key)
                for key in WINDOWS
            },
        }
        row["selection_score"] = _template_selection_score(row)
        rows.append(row)

    rows.sort(key=lambda row: (-row["selection_score"], row["template_id"]))
    pool_ids = [row["template_id"] for row in rows[:META_POOL_SIZE]]
    pool_base = {name: base_streams[name] for name in pool_ids}
    pool_stress = {name: stress_streams[name] for name in pool_ids}

    meta_candidates: list[dict] = []
    selection_start, selection_end = WINDOWS["selection_full"]
    for lookback in (20, 60, 120):
        for rebalance in (1, 5):
            for count in (1, 2, 3):
                series, _ = dynamic_rotate(
                    pool_base,
                    meta_lookback=lookback,
                    rebalance=rebalance,
                    count=count,
                    switch_cost_bps=BASE_COST_BPS,
                )
                selection_metrics = _metrics(
                    series.loc[
                        pd.Timestamp(selection_start) : pd.Timestamp(selection_end)
                    ]
                )
                score = (
                    4.0 * selection_metrics["annualized_return"]
                    + 0.5 * selection_metrics["sharpe"]
                    - 2.0 * abs(selection_metrics["max_drawdown"])
                )
                meta_candidates.append(
                    {
                        "meta_lookback": lookback,
                        "rebalance": rebalance,
                        "count": count,
                        "score": score,
                        "selection_metrics": selection_metrics,
                    }
                )
    meta_candidates.sort(
        key=lambda row: (
            -row["score"],
            row["meta_lookback"],
            row["rebalance"],
            row["count"],
        )
    )
    chosen_meta = meta_candidates[0]

    chosen_base, base_audit = dynamic_rotate(
        pool_base,
        meta_lookback=chosen_meta["meta_lookback"],
        rebalance=chosen_meta["rebalance"],
        count=chosen_meta["count"],
        switch_cost_bps=BASE_COST_BPS,
    )
    chosen_stress, _ = dynamic_rotate(
        pool_stress,
        meta_lookback=chosen_meta["meta_lookback"],
        rebalance=chosen_meta["rebalance"],
        count=chosen_meta["count"],
        switch_cost_bps=STRESS_COST_BPS,
    )
    for name in pool_ids:
        extreme_streams[name], _ = simulate_template(
            returns,
            exchanges,
            template_lookup[name],
            cost_bps=EXTREME_COST_BPS,
        )
    chosen_extreme, _ = dynamic_rotate(
        extreme_streams,
        meta_lookback=chosen_meta["meta_lookback"],
        rebalance=chosen_meta["rebalance"],
        count=chosen_meta["count"],
        switch_cost_bps=EXTREME_COST_BPS,
    )

    base_windows = {name: _window_metrics(chosen_base, name) for name in WINDOWS}
    stress_windows = {name: _window_metrics(chosen_stress, name) for name in WINDOWS}
    extreme_windows = {name: _window_metrics(chosen_extreme, name) for name in WINDOWS}
    effective_gross = max(
        (template_lookup[name].gross_leverage for name in pool_ids),
        default=0.0,
    )
    full_recent = base_windows["full_recent"]
    stress_recent = stress_windows["full_recent"]
    target_met = bool(
        full_recent["annualized_return"] >= 1.0
        and full_recent["max_drawdown"] > -0.30
        and stress_recent["annualized_return"] > 0.0
        and full_recent["active_days"] >= 50
        and effective_gross <= MAX_GROSS_LEVERAGE
    )

    family_contribution: dict[str, dict] = {}
    for name in pool_ids:
        family = template_lookup[name].family
        item = family_contribution.setdefault(
            family, {"templates": 0, "best_full_recent_annualized_return": -999.0}
        )
        item["templates"] += 1
        item["best_full_recent_annualized_return"] = max(
            item["best_full_recent_annualized_return"],
            _window_metrics(base_streams[name], "full_recent")["annualized_return"],
        )

    selected_counts: dict[str, int] = {}
    for item in base_audit:
        for name in item["selected"]:
            selected_counts[name] = selected_counts.get(name, 0) + 1

    report = {
        "source": "AKShare/Sina continuous daily Chinese commodity futures",
        "role": "L3 return-target discovery only; production requires specific contracts",
        "historical_l1_available": False,
        "continuous_roll_series": True,
        "pristine_final_oos": False,
        "windows": WINDOWS,
        "coverage": coverage,
        "base_cost_bps_one_way": BASE_COST_BPS,
        "stress_cost_bps_one_way": STRESS_COST_BPS,
        "extreme_cost_bps_one_way": EXTREME_COST_BPS,
        "template_count": len(templates),
        "template_results": rows,
        "selection": {
            "pool_ids": pool_ids,
            "meta_lookback": chosen_meta["meta_lookback"],
            "rebalance": chosen_meta["rebalance"],
            "count": chosen_meta["count"],
            "portfolio_gross_multiplier": 1.0,
            "effective_gross_leverage": float(effective_gross),
            "selection_score": float(chosen_meta["score"]),
            "selected_day_counts": selected_counts,
        },
        "base": base_windows,
        "stress": stress_windows,
        "extreme": extreme_windows,
        "family_contribution": family_contribution,
        "target": {
            "annualized_return": 1.0,
            "max_drawdown": -0.30,
            "gross_leverage_cap": MAX_GROSS_LEVERAGE,
            "minimum_active_days": 50,
            "stress_must_be_positive": True,
            "target_met": target_met,
        },
    }
    return report


def _write_template_csv(report: dict, path: Path) -> None:
    records: list[dict] = []
    for row in report["template_results"]:
        record = {
            **row["template"],
            "template_id": row["template_id"],
            "selection_score": row["selection_score"],
            "annual_turnover": row["annual_turnover"],
        }
        for scenario in ("base", "stress"):
            for window in ("train", "validation", "oos", "full_recent"):
                item = row[scenario][window]
                record[f"{scenario}_{window}_annualized_return"] = item[
                    "annualized_return"
                ]
                record[f"{scenario}_{window}_max_drawdown"] = item[
                    "max_drawdown"
                ]
        records.append(record)
    pd.DataFrame(records).to_csv(path, index=False)


def main() -> None:
    source = Path("runtime/broad_daily_universe.csv")
    if not source.exists():
        raise SystemExit("broad daily universe missing; run fetch_broad_daily_universe.py")
    report = evaluate(pd.read_csv(source))
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    (output / "return_target_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_template_csv(report, output / "return_target_template_results.csv")
    print(
        json.dumps(
            {
                "coverage": report["coverage"],
                "selection": report["selection"],
                "base_full_recent": report["base"]["full_recent"],
                "stress_full_recent": report["stress"]["full_recent"],
                "extreme_full_recent": report["extreme"]["full_recent"],
                "base_oos": report["base"]["oos"],
                "target": report["target"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
