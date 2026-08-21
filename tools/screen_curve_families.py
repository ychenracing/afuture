#!/usr/bin/env python3
"""用两年真实合约日线快速筛选质变型期限结构 Alpha。

本脚本只做策略家族筛选，不是生产晋级。开发期再拆 Train/Validation，候选参数在
运行前固定；最后锁定区间只做诊断，不反向选择家族或参数。通过筛选的家族仍需
接入 TradingEngine、执行完整成本/鲁棒性回放后才能考虑生产默认。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import date
import json
from pathlib import Path

from afuture.curve_research import CurveFamilyConfig, CurveFamilyResearch
from tools.run_real_two_year_research import (
    DEFAULT_END,
    DEFAULT_START,
    build_research_inputs,
    fetch_history,
)


def candidate_grid() -> tuple[CurveFamilyConfig, ...]:
    """少量预注册策略族；不在看到结果后扩展网格。"""
    rows: list[CurveFamilyConfig] = []

    # Scale-invariant calendar-spread mean reversion + EGARCH paper inspired
    # minimum volatility regime. Maximum-volatility guard belongs to production
    # risk and is intentionally not optimized here.
    for minimum_volatility in (0.0, 0.2, 0.4):
        rows.append(
            CurveFamilyConfig(
                "log_ratio_mean_reversion",
                fast_window=5,
                slow_window=30,
                mean_window=30,
                entry_z=1.5,
                exit_z=0.5,
                min_volatility_percentile=minimum_volatility,
                rebalance_samples=1,
            )
        )

    # Rossi 2025: weekly F1-F2 return reversal; position is spread-neutral.
    for minimum_volatility in (0.0, 0.2, 0.4):
        rows.append(
            CurveFamilyConfig(
                "basis_reversal",
                fast_window=5,
                slow_window=20,
                mean_window=20,
                min_volatility_percentile=minimum_volatility,
                rebalance_samples=5,
            )
        )

    # Boons 2019 is an outright cross-sectional signal. These two rows test only
    # the conservative spread-neutral adaptation; failure is not evidence that
    # the original outright anomaly is absent.
    for slow_window in (60, 120):
        rows.append(
            CurveFamilyConfig(
                "basis_momentum",
                fast_window=5,
                slow_window=slow_window,
                mean_window=30,
                rebalance_samples=20,
            )
        )

    # Lightweight causal proxy for the slow-momentum / fast-regime-switch idea.
    for severity in (1.0, 1.5, 2.0):
        rows.append(
            CurveFamilyConfig(
                "slow_momentum_fast_reversion",
                fast_window=5,
                slow_window=60,
                mean_window=30,
                change_severity=severity,
                rebalance_samples=1,
            )
        )
    return tuple(rows)


def _candidate_id(config: CurveFamilyConfig) -> str:
    if config.family in {"log_ratio_mean_reversion", "basis_reversal"}:
        return f"{config.family}:minvol={config.min_volatility_percentile:.2f}"
    if config.family == "basis_momentum":
        return f"{config.family}:slow={config.slow_window}"
    return f"{config.family}:severity={config.change_severity:.2f}"


def _acceptable(train: dict, validation: dict, cost_validation: dict) -> bool:
    """开发期硬门；不允许单一高 Sharpe 或单品种贡献掩盖负收益。"""
    return bool(
        float(train.get("total_return", 0.0)) > 0
        and float(validation.get("total_return", 0.0)) > 0
        and float(cost_validation.get("total_return", 0.0)) > 0
        and float(validation.get("sharpe", 0.0)) > 0
        and int(validation.get("trade_count", 0)) >= 6
        and float(train.get("positive_product_ratio", 0.0)) >= 0.4
        and float(validation.get("positive_product_ratio", 0.0)) >= 0.4
    )


def _score(train: dict, validation: dict, cost_validation: dict) -> float:
    """收益、回撤、成本后表现共同计分；Validation 权重更高。"""
    def component(metrics: dict) -> float:
        annualized = float(metrics.get("annualized_return", 0.0))
        drawdown = abs(float(metrics.get("max_drawdown", 0.0)))
        sharpe = float(metrics.get("sharpe", 0.0))
        return annualized - drawdown + 0.10 * sharpe

    return (
        0.25 * component(train)
        + 0.50 * component(validation)
        + 0.25 * component(cost_validation)
    )


def run_screen(rows_by_symbol, start: date, end: date) -> dict:
    ticks, specs, catalog, coverage = build_research_inputs(
        rows_by_symbol,
        start,
        end,
    )
    days = sorted({tick.trading_day for tick in ticks})
    if len(days) < 360:
        raise RuntimeError(f"insufficient real-data trading days: {len(days)}")

    locked_count = min(120, max(80, len(days) // 4))
    development = days[:-locked_count]
    locked = days[-locked_count:]
    validation_count = min(120, max(80, len(development) // 3))
    train = development[:-validation_count]
    validation = development[-validation_count:]

    researcher = CurveFamilyResearch(
        ticks,
        catalog,
        specs,
        min_days_to_expiry=20,
    )
    rows: list[dict] = []
    family_support: dict[str, int] = {}

    for config in candidate_grid():
        train_metrics = researcher.run(config, set(train))
        validation_metrics = researcher.run(config, set(validation))
        cost_validation = researcher.run(
            replace(config, slippage_ticks=2),
            set(validation),
        )
        accepted = _acceptable(
            train_metrics,
            validation_metrics,
            cost_validation,
        )
        if accepted:
            family_support[config.family] = family_support.get(config.family, 0) + 1
        rows.append(
            {
                "id": _candidate_id(config),
                "config": asdict(config),
                "accepted": accepted,
                "score": _score(
                    train_metrics,
                    validation_metrics,
                    cost_validation,
                ),
                "train": train_metrics,
                "validation": validation_metrics,
                "validation_2x_slippage": cost_validation,
            }
        )

    # 一个参数孤点不能晋级。每个候选家族都预注册了至少两个邻近配置，因此要求
    # 同一家族至少两个开发候选同时通过，才能认为存在稳定区域。
    stable = [
        row
        for row in rows
        if row["accepted"]
        and family_support.get(row["config"]["family"], 0) >= 2
    ]
    selected = max(stable, key=lambda row: row["score"]) if stable else None

    locked_metrics = None
    locked_cost = None
    if selected is not None:
        frozen = CurveFamilyConfig(**selected["config"])
        locked_metrics = researcher.run(frozen, set(locked))
        locked_cost = researcher.run(
            replace(frozen, slippage_ticks=2),
            set(locked),
        )

    return {
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trading_days": len(days),
            "train_days": len(train),
            "validation_days": len(validation),
            "locked_diagnostic_days": len(locked),
            "locked_start": locked[0],
            "locked_end": locked[-1],
        },
        "data": {
            "source": "Sina Finance contract daily bars",
            "historical_l1_available": False,
            "coverage": coverage,
            "products": list(researcher.products),
            "contracts": len(catalog),
        },
        "method": {
            "signal_execution_lag": "signal observed through day t; position earns from t to next observed open",
            "roll_jump_pnl": False,
            "candidate_count": len(rows),
            "minimum_family_support": 2,
            "locked_interval_used_for_selection": False,
            "locked_interval_is_pristine": False,
            "selection_rule": "positive train + validation + 2x-slippage validation; >=40% positive products; >=2 accepted neighbors in same family",
        },
        "family_support": family_support,
        "candidates": rows,
        "selected": (
            {
                "id": selected["id"],
                "config": selected["config"],
                "score": selected["score"],
            }
            if selected is not None
            else None
        ),
        "stable_family_found": selected is not None,
        "locked_diagnostic": locked_metrics,
        "locked_diagnostic_2x_slippage": locked_cost,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--cache", default="runtime/real_2y_cache")
    parser.add_argument(
        "--output",
        default="runtime/curve_family_screening.json",
    )
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start >= end:
        raise ValueError("start must be before end")

    rows_by_symbol, manifest = fetch_history(
        Path(args.cache),
        start,
        end,
    )
    result = run_screen(rows_by_symbol, start, end)
    result["download_manifest"] = manifest
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "stable_family_found": result["stable_family_found"],
                "selected": result["selected"],
                "locked_diagnostic": result["locked_diagnostic"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
