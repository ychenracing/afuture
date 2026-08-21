"""最终 Auto Portfolio 的 Walk-forward/OOS/鲁棒性研究。

与单 pair 轻量研究不同，本模块直接复用 TradingEngine + AutoPairManager + RiskManager +
PairExecutor + SimBroker，验证最终生产组合的自动发现、资金竞争、动态手数和生命周期。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import timedelta
from itertools import groupby
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

from .auto import AutoPairManager
from .broker.sim import SimBroker
from .calibration import ParameterCalibrator
from .engine import TradingEngine
from .models import ContractInfo, ContractSpec, FeeSpec, Tick
from .report import calculate_performance
from .risk import RiskManager
from .state import StateStore


@dataclass(frozen=True)
class AutoPortfolioResearchConfig:
    train_days: int = 120
    validation_days: int = 40
    oos_days: int = 40
    step_days: int = 40
    parameter_grid: tuple[dict, ...] = ()
    cost_stress_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)
    depth_haircuts: tuple[float, ...] = (1.0, 0.5)
    extra_latency_ticks: tuple[int, ...] = (0, 1, 2)
    extra_impact_ticks: tuple[int, ...] = (0, 1)
    data_gap_rates: tuple[float, ...] = (0.02, 0.05)
    quote_skew_seconds: tuple[float, ...] = (0.5, 1.0, 2.0)
    activity_missing_rates: tuple[float, ...] = (0.02, 0.05)
    min_selection_trades: int = 0
    run_post_analysis: bool = True


@dataclass(frozen=True)
class AutoPortfolioFold:
    parameters: dict
    train_metrics: dict
    validation_metrics: dict
    oos_metrics: dict
    oos_days: tuple[str, ...]


@dataclass(frozen=True)
class AutoPortfolioResearchResult:
    folds: list[AutoPortfolioFold]
    selected_parameters: dict
    stress_results: dict[float, dict]
    robustness: dict[str, object]


class AutoPortfolioRunner:
    """严格按时间顺序选择全局参数，并用完整 Auto 交易链执行 OOS。"""

    MAX_RESEARCH_RISK_BUDGET = 0.02
    MAX_RESEARCH_PAIR_VOLUME = 20
    _INVALID_SCORE = -1.0e12

    def __init__(self, base_config) -> None:
        if not base_config.auto.enabled:
            raise ValueError("accept-auto requires auto.enabled=true")
        self.base = base_config

    def parameter_grid(
        self,
        research: AutoPortfolioResearchConfig,
    ) -> list[dict]:
        """返回预先限定的小型全局参数邻域，不做高维全空间搜索。

        默认 CLI 不优化风险参数；两年真实数据研究可以显式传入受上限约束的
        ``risk_budget_ratio``/``max_pair_volume``，并且仍只能用 Train+Validation 选择。
        """
        if research.parameter_grid:
            return [dict(row) for row in research.parameter_grid]
        base = self._parameter_row(self.base.auto)
        rows = [dict(base)]

        def add(**updates) -> None:
            row = dict(base)
            row.update(updates)
            if not 0 <= row["exit_z"] < row["entry_z"] < self.base.auto.stop_z:
                return
            key = tuple(sorted(row.items()))
            if key not in {tuple(sorted(existing.items())) for existing in rows}:
                rows.append(row)

        add(lookback=max(2, int(round(base["lookback"] * 0.8))))
        add(lookback=max(2, int(round(base["lookback"] * 1.2))))
        add(entry_z=max(base["exit_z"] + 0.05, base["entry_z"] * 0.9))
        add(entry_z=min(self.base.auto.stop_z - 0.05, base["entry_z"] * 1.1))
        add(exit_z=max(0.0, base["exit_z"] * 0.9))
        add(exit_z=min(base["entry_z"] - 0.05, base["exit_z"] * 1.1))
        if base["min_net_edge"] > 0:
            add(min_net_edge=base["min_net_edge"] * 0.8)
            add(min_net_edge=base["min_net_edge"] * 1.2)
        if base["min_stationarity_score"] > 0:
            add(min_stationarity_score=max(0.0, base["min_stationarity_score"] * 0.8))
            add(min_stationarity_score=min(1.0, base["min_stationarity_score"] * 1.2))
        add(max_half_life=max(0.1, base["max_half_life"] * 0.8))
        add(max_half_life=base["max_half_life"] * 1.2)
        return rows

    def run(
        self,
        ticks: list[Tick],
        research: AutoPortfolioResearchConfig,
    ) -> AutoPortfolioResearchResult:
        self._validate(research)
        days = sorted({row.trading_day for row in ticks})
        total = research.train_days + research.validation_days + research.oos_days
        grid = self.parameter_grid(research)
        folds: list[AutoPortfolioFold] = []
        selected_rows: list[dict] = []

        for start in range(0, max(0, len(days) - total + 1), research.step_days):
            train_end = start + research.train_days
            validation_end = train_end + research.validation_days
            oos_end = validation_end + research.oos_days
            if oos_end > len(days):
                break
            train_days = days[start:train_end]
            validation_days = days[train_end:validation_end]
            oos_days = days[validation_end:oos_end]
            train_ticks = self._slice(ticks, train_days)
            validation_ticks = self._slice(ticks, validation_days)
            oos_ticks = self._slice(ticks, oos_days)

            candidates: list[dict] = []
            for parameters in grid:
                candidate = self._config_with_parameters(parameters)
                train_metrics = self._run_portfolio(candidate, train_ticks)
                validation_metrics = self._run_portfolio(candidate, validation_ticks)
                safe = self._metrics_acceptable(
                    train_metrics,
                    min_trades=research.min_selection_trades,
                ) and self._metrics_acceptable(
                    validation_metrics,
                    min_trades=research.min_selection_trades,
                )
                score = (
                    0.35 * self._score(train_metrics) + 0.65 * self._score(validation_metrics)
                    if safe
                    else self._INVALID_SCORE
                )
                candidates.append(
                    {
                        **parameters,
                        "score": score,
                        "max_drawdown": max(
                            abs(float(train_metrics.get("max_drawdown", 0.0))),
                            abs(float(validation_metrics.get("max_drawdown", 0.0))),
                        ),
                        "_train": train_metrics,
                        "_validation": validation_metrics,
                    }
                )

            selected = ParameterCalibrator().select_best(candidates)
            if selected is None or float(selected.get("score", self._INVALID_SCORE)) <= self._INVALID_SCORE / 2:
                continue
            parameters = {
                key: selected[key]
                for key in (
                    "lookback",
                    "entry_z",
                    "exit_z",
                    "min_net_edge",
                    "min_stationarity_score",
                    "max_half_life",
                    "risk_budget_ratio",
                    "max_pair_volume",
                )
                if key in selected
            }
            oos_metrics = self._run_portfolio(self._config_with_parameters(parameters), oos_ticks)
            folds.append(
                AutoPortfolioFold(
                    parameters=parameters,
                    train_metrics=selected["_train"],
                    validation_metrics=selected["_validation"],
                    oos_metrics=oos_metrics,
                    oos_days=tuple(oos_days),
                )
            )
            selected_rows.append(parameters)

        selected_parameters = selected_rows[-1] if selected_rows else self._parameter_row(self.base.auto)
        if research.run_post_analysis:
            final_config = self._config_with_parameters(selected_parameters)
            stress_results = {
                float(multiplier): self._run_portfolio(
                    self._cost_stress(final_config, float(multiplier)), ticks
                )
                for multiplier in research.cost_stress_multipliers
            }
            robustness = self._robustness(final_config, ticks, folds, research)
        else:
            stress_results = {}
            robustness = {}
        return AutoPortfolioResearchResult(
            folds=folds,
            selected_parameters=selected_parameters,
            stress_results=stress_results,
            robustness=robustness,
        )

    def _robustness(self, config, ticks, folds, research) -> dict[str, object]:
        products = sorted({item.product.lower() for item in config.contract_catalog})
        leave_one: dict[str, dict] = {}
        single: dict[str, dict] = {}
        for product in products:
            leave_one[product] = self._run_portfolio(
                self._with_products(config, tuple(item for item in config.auto.products if item.lower() != product)),
                ticks,
            )
            single[product] = self._run_portfolio(
                self._with_products(config, (product,)), ticks
            )

        remove_best = self._remove_best_period(config, ticks, folds)
        depth = {
            str(value): self._run_portfolio(config, self._haircut_depth(ticks, value))
            for value in research.depth_haircuts
        }
        latency = {
            str(value): self._run_portfolio(replace(config, latency_ticks=config.latency_ticks + value), ticks)
            for value in research.extra_latency_ticks
        }
        impact = {
            str(value): self._run_portfolio(
                replace(config, market_impact_ticks=config.market_impact_ticks + value), ticks
            )
            for value in research.extra_impact_ticks
        }
        data_gap = {
            self._ratio_key(value): self._run_portfolio(config, self._drop_data(ticks, value))
            for value in research.data_gap_rates
        }
        quote_skew = {
            str(value): self._run_portfolio(config, self._skew_far_legs(ticks, config.contract_catalog, value))
            for value in research.quote_skew_seconds
        }
        activity_missing = {
            self._ratio_key(value): self._run_portfolio(config, self._remove_activity(ticks, value))
            for value in research.activity_missing_rates
        }
        return {
            "leave_one_product_out": leave_one,
            "single_product": single,
            "remove_best_period": remove_best,
            "depth_haircut": depth,
            "latency": latency,
            "market_impact": impact,
            "data_gap": data_gap,
            "quote_skew": quote_skew,
            "activity_missing": activity_missing,
        }

    def _remove_best_period(self, config, ticks, folds) -> dict:
        if not folds:
            return self._run_portfolio(config, ticks)
        best = max(folds, key=lambda fold: float(fold.oos_metrics.get("total_return", 0.0)))
        excluded = set(best.oos_days)
        return self._run_portfolio(config, [row for row in ticks if row.trading_day not in excluded])

    def _run_portfolio(self, config, ticks: list[Tick]) -> dict:
        if not ticks:
            return self._empty_metrics(config.initial_capital)
        with TemporaryDirectory(prefix="afuture-auto-research-") as temp:
            root = Path(temp)
            broker = SimBroker(
                config.initial_capital,
                config.contracts,
                slippage_ticks=config.slippage_ticks,
                conservative=True,
                latency_ticks=config.latency_ticks,
                market_impact_ticks=config.market_impact_ticks,
                contract_catalog=config.contract_catalog,
            )
            broker._trading_day = ticks[0].trading_day
            engine = TradingEngine(
                broker,
                config.pairs,
                config.contracts,
                RiskManager(config.risk),
                StateStore(root / "state.json"),
                auto_flatten_imbalance=config.auto_flatten_imbalance,
                aggressive_ticks=config.aggressive_ticks,
                slippage_ticks=config.slippage_ticks,
                legging_timeout_seconds=config.legging_timeout_seconds,
                auto_manager=AutoPairManager(config.auto),
                require_live_metadata=False,
                historical_mode=True,
            )
            equity_curve: list[tuple[str, float]] = []
            engine.start()
            try:
                for _, group in groupby(sorted(ticks, key=lambda row: row.timestamp), key=lambda row: row.timestamp):
                    batch = list(group)
                    for row in batch:
                        broker.publish_tick(row)
                    engine.run_once()
                    equity_curve.append((batch[-1].trading_day, broker.get_account().equity))

                pre_terminal_positions = len(broker.get_positions())
                halted = bool(engine.halted or engine.state.kill_switch)
                kill_reason = str(engine.state.kill_reason or "")
                runtime_mode = str(engine.state.runtime_mode or "")
                final_day = ticks[-1].trading_day
                terminal_success, terminal_reason = self._terminal_liquidate(
                    engine,
                    broker,
                    final_day,
                )
                if terminal_success:
                    equity_curve.append((final_day, broker.get_account().equity))

                trades = broker.get_trades()
                metrics = calculate_performance(
                    equity_curve,
                    initial_capital=config.initial_capital,
                    trade_count=len(trades),
                )
                metrics["commission_total"] = sum(float(row.commission) for row in trades)
                metrics["pre_terminal_position_count"] = pre_terminal_positions
                metrics["final_position_count"] = len(broker.get_positions())
                metrics["halted"] = halted
                metrics["runtime_mode"] = runtime_mode
                metrics["kill_reason"] = kill_reason
                metrics["terminal_liquidation_success"] = terminal_success
                metrics["terminal_liquidation_reason"] = terminal_reason
                return metrics
            finally:
                engine.stop()

    @staticmethod
    def _terminal_liquidate(engine, broker, final_trading_day: str) -> tuple[bool, str]:
        """研究窗口末只用当日最后可见报价结清仓位，避免跨窗口免费丢弃持仓。

        终端先撤销所有未完成委托；随后只调用现有只减仓执行器，不改变生产
        TradingEngine 的停机语义。没有窗口末真实报价、存在未来报价依赖或无法
        完整结清时均失败关闭，禁止复制最后一条行情来制造未来流动性。
        """
        for order in broker.get_active_orders():
            broker.cancel_order(order.order_id)
        broker.poll_events()
        if broker.get_active_orders():
            return False, "terminal liquidation could not cancel active orders"

        positions = [position for position in broker.get_positions() if not position.empty]
        if not positions:
            return True, ""
        if int(getattr(broker, "latency_ticks", 0)) > 0:
            return False, "terminal liquidation requires a future quote under latency model"

        position_symbols = {position.symbol for position in positions}
        covered_symbols: set[str] = set()
        for pair in engine.pairs.values():
            pair_symbols = {pair.near_symbol, pair.far_symbol}
            if not (pair_symbols & position_symbols):
                continue
            near = engine.quotes.get(pair.near_symbol)
            far = engine.quotes.get(pair.far_symbol)
            if near is None or far is None:
                return False, f"terminal quote missing for pair: {pair.pair_id}"
            if near.trading_day != final_trading_day or far.trading_day != final_trading_day:
                return False, f"terminal quote is not from final trading day: {pair.pair_id}"
            try:
                engine.executor.flatten_imbalance(pair, near, far)
            except Exception as exc:
                return False, f"terminal liquidation submission failed for {pair.pair_id}: {exc}"
            covered_symbols.update(pair_symbols)

        uncovered = position_symbols - covered_symbols
        if uncovered:
            return False, f"terminal positions are not mapped to managed pairs: {sorted(uncovered)}"

        broker.poll_events()
        if broker.get_active_orders():
            for order in broker.get_active_orders():
                broker.cancel_order(order.order_id)
            broker.poll_events()
            return False, "terminal liquidation left active orders"

        remaining = [position for position in broker.get_positions() if not position.empty]
        if remaining:
            symbols = sorted(position.symbol for position in remaining)
            return False, f"terminal liquidation left positions: {symbols}"
        return True, ""

    def _metrics_acceptable(self, metrics: dict, *, min_trades: int = 0) -> bool:
        """搜索阶段的硬门：停机、残仓、无证据或触及最大回撤都不得晋级。"""
        if bool(metrics.get("halted", False)):
            return False
        if not bool(metrics.get("terminal_liquidation_success", True)):
            return False
        if int(metrics.get("final_position_count", 0)) != 0:
            return False
        if int(metrics.get("trade_count", 0)) < max(0, int(min_trades)):
            return False
        drawdown = abs(float(metrics.get("max_drawdown", 0.0)))
        return drawdown < float(self.base.risk.max_total_drawdown_ratio)

    def _config_with_parameters(self, parameters: dict):
        """应用离线研究参数；风险缩放有硬上限，不能通过无边界杠杆追收益。"""
        auto_values = {
            key: value
            for key, value in parameters.items()
            if key in self.base.auto.__dataclass_fields__
        }
        if "max_pair_volume" in auto_values:
            volume = int(auto_values["max_pair_volume"])
            if volume <= 0 or volume > self.MAX_RESEARCH_PAIR_VOLUME:
                raise ValueError("research max_pair_volume is outside bounded range")
            auto_values["max_pair_volume"] = volume

        risk_values = {}
        if "risk_budget_ratio" in parameters:
            ratio = float(parameters["risk_budget_ratio"])
            if ratio <= 0 or ratio > self.MAX_RESEARCH_RISK_BUDGET:
                raise ValueError("research risk_budget_ratio is outside bounded range")
            risk_values["risk_budget_ratio"] = ratio

        return replace(
            self.base,
            auto=replace(self.base.auto, **auto_values),
            risk=replace(self.base.risk, **risk_values),
        )

    @staticmethod
    def _parameter_row(auto) -> dict:
        return {
            "lookback": auto.lookback,
            "entry_z": auto.entry_z,
            "exit_z": auto.exit_z,
            "min_net_edge": auto.min_net_edge,
            "min_stationarity_score": auto.min_stationarity_score,
            "max_half_life": auto.max_half_life,
        }

    def _cost_stress(self, config, multiplier: float):
        specs = {symbol: self._scale_spec(row, multiplier) for symbol, row in config.contracts.items()}
        auto = replace(config.auto, legging_buffer=config.auto.legging_buffer * multiplier)
        slippage = max(config.slippage_ticks, int(round(config.slippage_ticks * multiplier)))
        return replace(config, contracts=specs, auto=auto, slippage_ticks=slippage)

    @staticmethod
    def _scale_spec(row: ContractSpec, multiplier: float) -> ContractSpec:
        fee = row.fee
        scaled_fee = FeeSpec(
            open_fixed=fee.open_fixed * multiplier,
            open_rate=fee.open_rate * multiplier,
            close_fixed=fee.close_fixed * multiplier,
            close_rate=fee.close_rate * multiplier,
            close_today_fixed=fee.close_today_fixed * multiplier,
            close_today_rate=fee.close_today_rate * multiplier,
        )
        return replace(row, fee=scaled_fee)

    @staticmethod
    def _with_products(config, products: tuple[str, ...]):
        if not products:
            products = ("__none__",)
        return replace(config, auto=replace(config.auto, products=products))

    @staticmethod
    def _haircut_depth(ticks: list[Tick], ratio: float) -> list[Tick]:
        ratio = max(0.0, float(ratio))
        return [
            replace(
                row,
                bid_volume=max(1.0, row.bid_volume * ratio),
                ask_volume=max(1.0, row.ask_volume * ratio),
            )
            for row in ticks
        ]

    @staticmethod
    def _drop_data(ticks: list[Tick], ratio: float) -> list[Tick]:
        """确定性删除少量 Tick，避免随机种子让 CI 不可复现。"""
        if ratio <= 0:
            return list(ticks)
        every = max(2, int(round(1.0 / ratio)))
        counters: dict[str, int] = defaultdict(int)
        result: list[Tick] = []
        for row in sorted(ticks, key=lambda item: (item.timestamp, item.symbol)):
            counters[row.symbol] += 1
            if counters[row.symbol] % every == 0:
                continue
            result.append(row)
        return result

    @staticmethod
    def _skew_far_legs(
        ticks: list[Tick], catalog: list[ContractInfo], seconds: float
    ) -> list[Tick]:
        """把每个品种较远月份向后移动，验证异步双腿行情不会制造虚假 Alpha。"""
        grouped: dict[tuple[str, str], list[ContractInfo]] = defaultdict(list)
        for item in catalog:
            grouped[(item.product.lower(), item.exchange.upper())].append(item)
        far_symbols: set[str] = set()
        for rows in grouped.values():
            rows.sort(key=lambda item: item.expiry)
            far_symbols.update(item.symbol for item in rows[1:])
        return [
            replace(row, timestamp=row.timestamp + timedelta(seconds=seconds))
            if row.symbol in far_symbols
            else row
            for row in ticks
        ]

    @staticmethod
    def _remove_activity(ticks: list[Tick], ratio: float) -> list[Tick]:
        if ratio <= 0:
            return list(ticks)
        every = max(2, int(round(1.0 / ratio)))
        counters: dict[str, int] = defaultdict(int)
        result: list[Tick] = []
        for row in sorted(ticks, key=lambda item: (item.timestamp, item.symbol)):
            counters[row.symbol] += 1
            if counters[row.symbol] % every == 0:
                result.append(replace(row, volume=0.0, open_interest=0.0))
            else:
                result.append(row)
        return result

    @staticmethod
    def _ratio_key(value: float) -> str:
        return f"{float(value):.6g}"

    @staticmethod
    def _slice(ticks: list[Tick], days: list[str]) -> list[Tick]:
        allowed = set(days)
        return [row for row in ticks if row.trading_day in allowed]

    @staticmethod
    def _score(metrics: dict) -> float:
        total_return = float(metrics.get("total_return", 0.0))
        drawdown = abs(float(metrics.get("max_drawdown", 0.0)))
        sharpe = float(metrics.get("sharpe", 0.0))
        if not isfinite(sharpe):
            sharpe = 0.0
        calmar = total_return / max(drawdown, 1e-6) if total_return else 0.0
        return 0.6 * calmar + 0.4 * sharpe

    @staticmethod
    def _empty_metrics(initial_capital: float) -> dict:
        return {
            "initial_capital": initial_capital,
            "final_equity": initial_capital,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "trading_days": 0,
            "trade_count": 0,
            "commission_total": 0.0,
            "pre_terminal_position_count": 0,
            "final_position_count": 0,
            "halted": False,
            "runtime_mode": "RUNNING",
            "kill_reason": "",
            "terminal_liquidation_success": True,
            "terminal_liquidation_reason": "",
        }

    @staticmethod
    def _validate(config: AutoPortfolioResearchConfig) -> None:
        if any(value <= 0 for value in (config.train_days, config.validation_days, config.oos_days, config.step_days)):
            raise ValueError("auto walk-forward windows must be positive")
        if config.min_selection_trades < 0:
            raise ValueError("min_selection_trades cannot be negative")
        if not config.cost_stress_multipliers or any(value <= 0 for value in config.cost_stress_multipliers):
            raise ValueError("cost stress multipliers must be positive")
        if any(not 0 < value <= 1 for value in config.depth_haircuts):
            raise ValueError("depth haircuts must be within (0, 1]")
        if any(value < 0 for value in config.extra_latency_ticks + config.extra_impact_ticks):
            raise ValueError("latency/impact stress cannot be negative")
        if any(not 0 <= value < 1 for value in config.data_gap_rates + config.activity_missing_rates):
            raise ValueError("data/activity stress ratios must be within [0, 1)")
        if any(value < 0 for value in config.quote_skew_seconds):
            raise ValueError("quote skew stress cannot be negative")
