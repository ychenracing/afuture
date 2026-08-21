#!/usr/bin/env python3
"""下载两年真实合约日线并执行严格 Auto Portfolio 调优。

研究窗口固定为 2024-08-21 至 2026-08-20。最后 120 个交易日作为锁定基准区间；
信号、机会质量、Regime/Carry 和风险缩放都只使用此前数据的 Train+Validation 选择。
历史基准区间在上一代策略研究中已经被观察过，因此新一代能力不能再把它描述成
“从未看过的最终 holdout”；真正新的证据必须来自滚动 OOS、后续 Shadow/实盘数据。
若年化目标未达到，脚本如实输出 target_met=false，不用基准区间反向扩大搜索空间。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from math import prod
from pathlib import Path
from time import sleep

from afuture.auto import AutoConfig
from afuture.auto_research import AutoPortfolioResearchConfig, AutoPortfolioRunner
from afuture.config import AppConfig
from afuture.models import ContractInfo
from afuture.real_data import (
    PRODUCT_DEFINITIONS,
    DailyBar,
    SinaDailyClient,
    contract_spec,
    contract_symbols,
    daily_bars_to_ticks,
)
from afuture.risk import RiskConfig


DEFAULT_START = date(2024, 8, 21)
DEFAULT_END = date(2026, 8, 20)
PRODUCTS = ("m", "rb", "TA", "c", "p")
REGIME_KEYS = (
    "min_persistence_score",
    "max_volatility_percentile",
    "max_trend_shift_z",
    "min_carry_reversal_z",
    "carry_reversal_weight",
)


def _bar_to_dict(row: DailyBar) -> dict:
    payload = asdict(row)
    payload["day"] = row.day.isoformat()
    return payload


def _bar_from_dict(row: dict) -> DailyBar:
    return DailyBar(
        symbol=str(row["symbol"]),
        day=date.fromisoformat(str(row["day"])),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        open_interest=float(row["open_interest"]),
        settle=float(row.get("settle", 0.0)),
    )


def fetch_history(cache_dir: Path, start: date, end: date) -> tuple[dict[str, list[DailyBar]], dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = SinaDailyClient(timeout_seconds=20.0, retries=3)
    rows_by_symbol: dict[str, list[DailyBar]] = {}
    manifest = {"source": "Sina Finance DailyKLine", "symbols": {}, "errors": {}}

    for product in PRODUCTS:
        definition = PRODUCT_DEFINITIONS[product]
        for symbol in contract_symbols(definition, start, end, warmup_days=180, far_month_buffer=8):
            cache_path = cache_dir / f"{symbol}.json"
            try:
                if cache_path.exists():
                    raw = json.loads(cache_path.read_text(encoding="utf-8"))
                    rows = [_bar_from_dict(item) for item in raw]
                else:
                    rows = client.fetch(symbol)
                    if rows:
                        cache_path.write_text(
                            json.dumps([_bar_to_dict(item) for item in rows], ensure_ascii=False),
                            encoding="utf-8",
                        )
                    sleep(0.12)
            except Exception as exc:
                manifest["errors"][symbol] = str(exc)
                continue
            if not rows:
                continue
            rows_by_symbol[symbol] = rows
            target_rows = [item for item in rows if start <= item.day <= end]
            manifest["symbols"][symbol] = {
                "product": product,
                "all_rows": len(rows),
                "target_rows": len(target_rows),
                "first": rows[0].day.isoformat(),
                "last": rows[-1].day.isoformat(),
            }
    return rows_by_symbol, manifest


def _contract_expiry(symbol: str, bars: list[DailyBar], end: date) -> date:
    """已到期合约使用真实最后行情日；未来合约使用交割月保守规则日期。"""
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) < 4:
        return max(row.day for row in bars)
    year = 2000 + int(digits[-4:-2])
    month = int(digits[-2:])
    if (year, month) <= (end.year, end.month):
        return max(row.day for row in bars)
    # 20 日只是研究用临近到期过滤边界，不参与价格、收益或 OOS 参数选择。
    return date(year, month, 20)


def build_research_inputs(rows_by_symbol: dict[str, list[DailyBar]], start: date, end: date):
    ticks = []
    specs = {}
    catalog: list[ContractInfo] = []
    coverage: dict[str, dict] = {}

    for product in PRODUCTS:
        definition = PRODUCT_DEFINITIONS[product]
        product_symbols = []
        for symbol, bars in sorted(rows_by_symbol.items()):
            if not symbol.upper().startswith(definition.product.upper()):
                continue
            target = [row for row in bars if start <= row.day <= end]
            if not target:
                continue
            converted = daily_bars_to_ticks(target, definition)
            if not converted.ticks:
                continue
            ticks.extend(converted.ticks)
            specs[symbol] = contract_spec(symbol, definition)
            expiry = _contract_expiry(symbol, bars, end)
            catalog.append(
                ContractInfo(
                    symbol=symbol,
                    exchange=definition.exchange,
                    product=definition.product,
                    expiry=expiry.isoformat(),
                    # 第一条真实日线是保守、可复现的挂牌可见性边界；即使官方实际
                    # listing 更早，也不会把未来尚无任何市场数据的合约提前泄漏进 Universe。
                    listing=min(row.day for row in bars).isoformat(),
                )
            )
            product_symbols.append(symbol)
        coverage[product] = {"contracts": len(product_symbols), "symbols": product_symbols}

    ticks.sort(key=lambda row: (row.timestamp, row.symbol))
    catalog.sort(key=lambda row: (row.product.lower(), row.expiry, row.symbol))
    return ticks, specs, catalog, coverage


def base_config(specs, catalog) -> AppConfig:
    auto = AutoConfig(
        enabled=True,
        products=PRODUCTS,
        exchanges=("DCE", "SHFE", "CZCE"),
        max_active_pairs=2,
        max_pairs_per_product=1,
        max_contracts_per_product=3,
        min_days_to_expiry=20,
        scan_interval_seconds=0.0,
        max_sync_seconds=2.0,
        lookback=30,
        entry_z=1.5,
        exit_z=0.5,
        stop_z=4.0,
        max_pair_volume=20,
        sample_seconds=0,
        max_holding_samples=40,
        structural_mean_shift_z=4.0,
        structural_vol_ratio=3.0,
        legging_buffer=50.0,
        min_volume=3000.0,
        min_open_interest=5000.0,
        min_liquidity_score=0.20,
        min_stationarity_score=0.005,
        max_half_life=60.0,
        min_net_edge=50.0,
        # 参考项目迁移能力默认关闭；只有开发集滚动 OOS 选择后才冻结。
        min_persistence_score=0.0,
        max_volatility_percentile=1.0,
        max_trend_shift_z=12.0,
        min_carry_reversal_z=0.0,
        carry_reversal_weight=0.0,
        slippage_ticks=1,
        session_windows=("09:00-15:00",),
    )
    risk = RiskConfig(
        max_margin_ratio=0.30,
        max_daily_loss_ratio=0.01,
        max_total_drawdown_ratio=0.08,
        max_open_pairs=2,
        max_contract_volume=20,
        max_quote_age_seconds=10.0,
        max_leg_skew_seconds=2.0,
        expiry_blackout_days=5,
        min_available_ratio=0.55,
        margin_estimate_buffer=1.25,
        max_orders_per_minute=20,
        min_depth_multiple=2.0,
        max_bid_ask_ticks=3.0,
        limit_distance_ticks=3.0,
        risk_budget_ratio=0.002,
        risk_sigma_multiplier=2.5,
        open_cooldown_minutes=0,
        close_blackout_minutes=0,
    )
    return AppConfig(
        mode="replay",
        initial_capital=500000.0,
        contracts=specs,
        pairs=[],
        risk=risk,
        ctp=None,
        slippage_ticks=1,
        aggressive_ticks=1,
        conservative_simulation=True,
        latency_ticks=0,
        market_impact_ticks=0,
        require_live_metadata=False,
        auto=auto,
        contract_catalog=catalog,
    )


def signal_grid(base: AppConfig) -> tuple[dict, ...]:
    rows = []
    for lookback in (15, 20, 30, 40, 60):
        for entry_z in (1.1, 1.3, 1.5, 1.8, 2.1):
            for exit_z in (0.25, 0.5):
                if exit_z >= entry_z:
                    continue
                rows.append(
                    {
                        "lookback": lookback,
                        "entry_z": entry_z,
                        "exit_z": exit_z,
                        "min_net_edge": 50.0,
                        "min_stationarity_score": 0.005,
                        "max_half_life": 60.0,
                        "risk_budget_ratio": 0.004,
                        "max_pair_volume": 20,
                    }
                )
    return tuple(rows)


def quality_grid(signal_parameters: dict) -> tuple[dict, ...]:
    """开发集第二阶段：固定 Z-score 结构，只搜索机会质量硬门。"""
    fixed = {
        key: signal_parameters[key]
        for key in ("lookback", "entry_z", "exit_z")
        if key in signal_parameters
    }
    rows = []
    for min_net_edge in (0.0, 50.0, 100.0):
        for stationarity in (0.0, 0.005, 0.01):
            for half_life in (30.0, 60.0, 120.0):
                rows.append(
                    {
                        **fixed,
                        "min_net_edge": min_net_edge,
                        "min_stationarity_score": stationarity,
                        "max_half_life": half_life,
                        "risk_budget_ratio": 0.004,
                        "max_pair_volume": 20,
                    }
                )
    return tuple(rows)


def regime_grid(quality_parameters: dict) -> tuple[dict, ...]:
    """开发集第三阶段：单轴搜索关系持久性、波动、变化点和曲线 carry。

    不做五维笛卡尔积。每个候选只改变一个 Regime/Carry 维度，让稳定区域选择和
    后续消融都能解释“哪一项能力提供了增量”，同时限制研究自由度。
    """
    fixed = {
        key: quality_parameters[key]
        for key in (
            "lookback",
            "entry_z",
            "exit_z",
            "min_net_edge",
            "min_stationarity_score",
            "max_half_life",
        )
        if key in quality_parameters
    }
    baseline = {
        **fixed,
        "min_persistence_score": 0.0,
        "max_volatility_percentile": 1.0,
        "max_trend_shift_z": 12.0,
        "min_carry_reversal_z": 0.0,
        "carry_reversal_weight": 0.0,
        "risk_budget_ratio": 0.004,
        "max_pair_volume": 20,
    }
    rows = [baseline]
    for key, values in (
        ("min_persistence_score", (0.34, 0.67)),
        ("max_volatility_percentile", (0.95, 0.85)),
        ("max_trend_shift_z", (4.0, 2.5)),
        ("min_carry_reversal_z", (0.5, 1.0)),
        ("carry_reversal_weight", (0.5, 1.0)),
    ):
        for value in values:
            row = dict(baseline)
            row[key] = value
            rows.append(row)
    return tuple(rows)


def risk_grid(selected_parameters: dict) -> tuple[dict, ...]:
    """开发集最后阶段：冻结信号、机会质量和 Regime，只缩放受限风险预算。"""
    fixed = {
        key: selected_parameters[key]
        for key in (
            "lookback",
            "entry_z",
            "exit_z",
            "min_net_edge",
            "min_stationarity_score",
            "max_half_life",
            *REGIME_KEYS,
        )
        if key in selected_parameters
    }
    for key, default in (
        ("min_persistence_score", 0.0),
        ("max_volatility_percentile", 1.0),
        ("max_trend_shift_z", 12.0),
        ("min_carry_reversal_z", 0.0),
        ("carry_reversal_weight", 0.0),
    ):
        fixed.setdefault(key, default)
    rows = []
    for ratio in (0.002, 0.004, 0.006, 0.008, 0.010, 0.015, 0.020):
        for volume in (5, 8, 12, 20):
            rows.append({**fixed, "risk_budget_ratio": ratio, "max_pair_volume": volume})
    return tuple(rows)


def regime_ablations(selected_parameters: dict) -> dict[str, dict]:
    """只关闭一个已选能力，用于开发集滚动 OOS 归因，不参与参数选择。"""
    disabled = {
        "persistence": {"min_persistence_score": 0.0},
        "volatility": {"max_volatility_percentile": 1.0},
        "trend_shift": {"max_trend_shift_z": 12.0},
        "carry": {
            "min_carry_reversal_z": 0.0,
            "carry_reversal_weight": 0.0,
        },
    }
    result: dict[str, dict] = {}
    for name, updates in disabled.items():
        row = dict(selected_parameters)
        row.update(updates)
        result[name] = row
    return result


def oos_summary(folds) -> dict:
    returns = [float(row.oos_metrics.get("total_return", 0.0)) for row in folds]
    days = sum(int(row.oos_metrics.get("trading_days", 0)) for row in folds)
    compounded = prod(1.0 + value for value in returns) - 1.0 if returns else 0.0
    annualized = (1.0 + compounded) ** (252.0 / days) - 1.0 if days > 0 and compounded > -1 else 0.0
    return {
        "folds": len(folds),
        "trading_days": days,
        "compounded_return": compounded,
        "annualized_return": annualized,
        "positive_fold_ratio": (sum(value > 0 for value in returns) / len(returns)) if returns else 0.0,
        "worst_drawdown": max((abs(float(row.oos_metrics.get("max_drawdown", 0.0))) for row in folds), default=0.0),
        "trade_count": sum(int(row.oos_metrics.get("trade_count", 0)) for row in folds),
    }


def _search_stage(runner, ticks, research_window: dict, grid: tuple[dict, ...]):
    return runner.run(
        ticks,
        AutoPortfolioResearchConfig(
            **research_window,
            parameter_grid=grid,
            min_selection_trades=1,
            run_post_analysis=False,
        ),
    )


def run_research(rows_by_symbol, start: date, end: date) -> dict:
    ticks, specs, catalog, coverage = build_research_inputs(rows_by_symbol, start, end)
    days = sorted({row.trading_day for row in ticks})
    if len(days) < 360:
        raise RuntimeError(f"insufficient real-data trading days: {len(days)}")
    holdout_count = min(120, max(80, len(days) // 4))
    dev_days = set(days[:-holdout_count])
    holdout_days = set(days[-holdout_count:])
    dev_ticks = [row for row in ticks if row.trading_day in dev_days]
    holdout_ticks = [row for row in ticks if row.trading_day in holdout_days]

    config = base_config(specs, catalog)
    runner = AutoPortfolioRunner(config)
    research_window = dict(train_days=160, validation_days=60, oos_days=60, step_days=60)

    baseline_grid = (
        {
            "lookback": config.auto.lookback,
            "entry_z": config.auto.entry_z,
            "exit_z": config.auto.exit_z,
            "min_net_edge": config.auto.min_net_edge,
            "min_stationarity_score": config.auto.min_stationarity_score,
            "max_half_life": config.auto.max_half_life,
            "min_persistence_score": config.auto.min_persistence_score,
            "max_volatility_percentile": config.auto.max_volatility_percentile,
            "max_trend_shift_z": config.auto.max_trend_shift_z,
            "min_carry_reversal_z": config.auto.min_carry_reversal_z,
            "carry_reversal_weight": config.auto.carry_reversal_weight,
            "risk_budget_ratio": config.risk.risk_budget_ratio,
            "max_pair_volume": config.auto.max_pair_volume,
        },
    )
    baseline = _search_stage(runner, dev_ticks, research_window, baseline_grid)
    stage_signal = _search_stage(runner, dev_ticks, research_window, signal_grid(config))
    stage_quality_grid = quality_grid(stage_signal.selected_parameters)
    stage_quality = _search_stage(runner, dev_ticks, research_window, stage_quality_grid)
    stage_regime_grid = regime_grid(stage_quality.selected_parameters)
    stage_regime = _search_stage(runner, dev_ticks, research_window, stage_regime_grid)
    stage_risk_grid = risk_grid(stage_regime.selected_parameters)
    stage_risk = _search_stage(runner, dev_ticks, research_window, stage_risk_grid)

    selection_valid = bool(
        stage_signal.folds
        and stage_quality.folds
        and stage_regime.folds
        and stage_risk.folds
    )
    frozen = runner._config_with_parameters(stage_risk.selected_parameters)

    # 消融只用于解释开发集滚动 OOS；结果不得反向修改已冻结参数。
    ablation_results = {
        name: _search_stage(runner, dev_ticks, research_window, (parameters,))
        for name, parameters in regime_ablations(stage_risk.selected_parameters).items()
    }

    # 参数冻结后才读取最后基准区间；该区间已在上一代研究中被观察过，不能再称
    # 为“全新 holdout”，也绝不根据这里的表现追加参数搜索。
    baseline_holdout = runner._run_portfolio(config, holdout_ticks)
    tuned_holdout = runner._run_portfolio(frozen, holdout_ticks)
    stress_15 = runner._run_portfolio(runner._cost_stress(frozen, 1.5), holdout_ticks)
    stress_20 = runner._run_portfolio(runner._cost_stress(frozen, 2.0), holdout_ticks)
    leave_one = {
        product: runner._run_portfolio(
            runner._with_products(
                frozen,
                tuple(item for item in frozen.auto.products if item.lower() != product.lower()),
            ),
            holdout_ticks,
        )
        for product in PRODUCTS
    }

    annualized = float(tuned_holdout.get("annualized_return", 0.0))
    drawdown = abs(float(tuned_holdout.get("max_drawdown", 0.0)))
    target_met = bool(
        selection_valid
        and annualized >= 1.0
        and drawdown <= 0.08
        and not bool(tuned_holdout.get("halted", False))
        and bool(tuned_holdout.get("terminal_liquidation_success", False))
        and int(tuned_holdout.get("final_position_count", 0)) == 0
        and float(stress_20.get("total_return", 0.0)) >= -0.02
        and not bool(stress_20.get("halted", False))
        and bool(stress_20.get("terminal_liquidation_success", False))
        and int(stress_20.get("final_position_count", 0)) == 0
    )

    return {
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trading_days": len(days),
            "development_days": len(dev_days),
            "holdout_days": len(holdout_days),
            "holdout_start": min(holdout_days),
            "holdout_end": max(holdout_days),
            "holdout_role": (
                "locked benchmark reused after prior-generation observation; "
                "not pristine for the new regime/carry feature generation"
            ),
        },
        "data": {
            "source": "Sina Finance contract daily bars",
            "real_fields": ["open", "high", "low", "close", "volume", "open_interest", "settle"],
            "historical_l1_available": False,
            "decision_price": "trading-day daily open",
            "activity_information": "previous trading day volume/open_interest",
            "execution_proxy": "bid=open-1 tick, ask=open+1 tick; SimBroker adds slippage; depth is prior-day-volume-derived proxy",
            "coverage": coverage,
            "contracts": len(catalog),
            "ticks": len(ticks),
        },
        "search": {
            "signal_candidates": len(signal_grid(config)),
            "quality_candidates": len(stage_quality_grid),
            "regime_candidates": len(stage_regime_grid),
            "risk_candidates": len(stage_risk_grid),
            "min_selection_trades": 1,
            "risk_budget_upper_bound": AutoPortfolioRunner.MAX_RESEARCH_RISK_BUDGET,
            "pair_volume_upper_bound": AutoPortfolioRunner.MAX_RESEARCH_PAIR_VOLUME,
            "holdout_used_for_optimization": False,
            "holdout_is_pristine_for_current_generation": False,
            "same_bar_close_lookahead": False,
            "historical_listing_filter": True,
            "regime_search_is_single_axis": True,
            "selection_valid": selection_valid,
            "search_exhausted": True,
        },
        "baseline_development_oos": oos_summary(baseline.folds),
        "signal_development_oos": oos_summary(stage_signal.folds),
        "quality_development_oos": oos_summary(stage_quality.folds),
        "regime_development_oos": oos_summary(stage_regime.folds),
        "regime_ablation_development_oos": {
            name: oos_summary(result.folds)
            for name, result in ablation_results.items()
        },
        "tuned_development_oos": oos_summary(stage_risk.folds),
        "selected_parameters": stage_risk.selected_parameters,
        "baseline_holdout": baseline_holdout,
        "tuned_holdout": tuned_holdout,
        "holdout_cost_stress": {"1.5": stress_15, "2.0": stress_20},
        "holdout_leave_one_product_out": leave_one,
        "target": {
            "annualized_return": 1.0,
            "max_drawdown": 0.08,
            "target_met": target_met,
            "observed_annualized_return": annualized,
            "observed_max_drawdown": drawdown,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--cache", default="runtime/real_2y_cache")
    parser.add_argument("--output", default="runtime/real_2y_research.json")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start >= end:
        raise ValueError("start must be before end")

    rows_by_symbol, manifest = fetch_history(Path(args.cache), start, end)
    result = run_research(rows_by_symbol, start, end)
    result["download_manifest"] = manifest
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["target"], ensure_ascii=False, indent=2))
    print(f"selected_parameters={result['selected_parameters']}")
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
