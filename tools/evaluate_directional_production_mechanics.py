"""Production-mechanics proxy for the frozen execution-aligned directional policy.

The historical 107.46% result is a float-notional signal/execution study. This evaluator
keeps the exact frozen weights but adds integer lots, contract multipliers, previous-day
activity selection, contract roll semantics, account drawdown/daily-loss behavior and an
explicit margin proxy. It never searches parameters and does not claim historical broker
margin schedules are known.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evaluate_execution_aligned_target as float_l4
from afuture.directional_acceptance import (
    DirectionalProductionAcceptance,
    ProductionMechanicsConfig,
    ProductionSimulationResult,
)

BASE_COST_BPS = 5.0
STRESS_COST_BPS = 15.0
BASE_MARGIN_PROXY = 0.12
STRESS_MARGIN_PROXY = 0.15
INITIAL_CAPITAL = 500000.0


def _simulation_report(
    result: ProductionSimulationResult,
    *,
    cost_bps: float,
    margin_rate_proxy: float,
) -> dict:
    returns = result.daily["daily_return"].astype(float) if not result.daily.empty else pd.Series(dtype=float)
    windows = {
        name: float_l4.aggressive._window_metrics(returns, name)
        for name in float_l4.WINDOWS
    }
    margin_reject_days = (
        int((result.daily["margin_reject"].astype(str) != "").sum())
        if not result.daily.empty
        else 0
    )
    halted = bool(result.daily["halted"].astype(bool).any()) if not result.daily.empty else False
    max_gross_ratio = 0.0
    if not result.daily.empty:
        equity = result.daily["equity"].replace(0.0, pd.NA)
        ratio = result.daily["gross_notional"].div(equity).replace([float("inf"), -float("inf")], pd.NA)
        max_gross_ratio = float(ratio.dropna().max()) if not ratio.dropna().empty else 0.0
    return {
        "cost_bps": float(cost_bps),
        "margin_rate_proxy": float(margin_rate_proxy),
        "margin_estimate_buffer": 1.25,
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": float(result.final_equity),
        "first_divergence": str(result.first_divergence),
        "halted": halted,
        "margin_reject_days": margin_reject_days,
        "max_realized_gross_notional_ratio": max_gross_ratio,
        "windows": windows,
    }


def evaluate_with_weights(
    specific_raw: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    float_report: dict | None = None,
) -> dict:
    base_sim = DirectionalProductionAcceptance(
        ProductionMechanicsConfig(
            initial_capital=INITIAL_CAPITAL,
            margin_rate_proxy=BASE_MARGIN_PROXY,
        )
    )
    stress_sim = DirectionalProductionAcceptance(
        ProductionMechanicsConfig(
            initial_capital=INITIAL_CAPITAL,
            margin_rate_proxy=STRESS_MARGIN_PROXY,
        )
    )
    base_result = base_sim.simulate(specific_raw, weights, cost_bps=BASE_COST_BPS)
    stress_result = stress_sim.simulate(specific_raw, weights, cost_bps=STRESS_COST_BPS)
    base = _simulation_report(
        base_result, cost_bps=BASE_COST_BPS, margin_rate_proxy=BASE_MARGIN_PROXY
    )
    stress = _simulation_report(
        stress_result, cost_bps=STRESS_COST_BPS, margin_rate_proxy=STRESS_MARGIN_PROXY
    )

    production_gap: dict[str, dict] = {}
    if float_report is not None:
        for label, production in (("base", base), ("stress", stress)):
            historical = float_report["next_open_execution"][label]["full_recent"]
            proxy = production["windows"]["full_recent"]
            production_gap[label] = {
                "float_annualized_return": float(historical["annualized_return"]),
                "production_proxy_annualized_return": float(proxy["annualized_return"]),
                "annualized_return_delta": float(proxy["annualized_return"] - historical["annualized_return"]),
                "float_max_drawdown": float(historical["max_drawdown"]),
                "production_proxy_max_drawdown": float(proxy["max_drawdown"]),
                "max_drawdown_delta": float(proxy["max_drawdown"] - historical["max_drawdown"]),
            }

    return {
        "role": "production-mechanics proxy acceptance for frozen execution-aligned directional policy",
        "selection_frozen": True,
        "parameter_search": False,
        "margin_is_historical_truth": False,
        "mechanics": {
            "integer_lots": True,
            "frozen_contract_multipliers": True,
            "previous_completed_activity": True,
            "reduction_before_open": True,
            "daily_loss_gate": True,
            "high_watermark_drawdown_gate": True,
            "permanent_halt_after_risk_breach": True,
        },
        "base": base,
        "stress": stress,
        "production_gap": production_gap,
        "limitations": [
            "historical daily L1 bid/ask/depth and partial fills are unavailable",
            "margin uses an explicit proxy rather than historical broker schedules",
            "daily bars cannot identify the exact intraday instant at which an account risk gate would fire",
        ],
        "_base_daily": base_result.daily,
        "_stress_daily": stress_result.daily,
    }


def evaluate(specific_raw: pd.DataFrame, continuous_raw: pd.DataFrame) -> dict:
    # Exact frozen 96-template policy; no new fit/search occurs here.
    weights = float_l4.generate_execution_signal_weights(continuous_raw)
    historical = float_l4.evaluate(specific_raw, continuous_raw)
    return evaluate_with_weights(specific_raw, weights, float_report=historical)


def _jsonable(report: dict) -> dict:
    return {key: value for key, value in report.items() if not key.startswith("_")}


def main() -> None:
    runtime = Path("runtime")
    specific_path = runtime / "return_target_specific_contracts.csv"
    continuous_path = runtime / "broad_daily_universe.csv"
    missing = [str(path) for path in (specific_path, continuous_path) if not path.exists()]
    if missing:
        raise SystemExit(f"directional production mechanics inputs missing: {missing}")
    report = evaluate(pd.read_csv(specific_path), pd.read_csv(continuous_path))
    report["_base_daily"].to_csv(runtime / "directional_production_base_daily.csv")
    report["_stress_daily"].to_csv(runtime / "directional_production_stress_daily.csv")
    output = runtime / "directional_production_mechanics_report.json"
    output.write_text(
        json.dumps(_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(_jsonable(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
