"""最终 Auto Portfolio 的 Walk-forward/OOS/鲁棒性研究。

与单 pair 轻量研究不同，本模块直接复用 TradingEngine + AutoPairManager + RiskManager +
PairExecutor + SimBroker，验证最终生产组合的自动发现、资金竞争、动态手数和生命周期。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import groupby
from math import isfinite
from tempfile import TemporaryDirectory
from pathlib import Path

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

    def __init__(self, base_config) -> None:
        if not base_config.auto.enabled:
            raise ValueError("accept-auto requires auto.enabled=true")
        self.base = base_config

    def run(
        self,
        ticks: list[Tick],
        research: AutoPortfolioResearchConfig,
    ) -> AutoPortfolioResearchResult:
        self._validate(research)
        days = sorted({row.trading_day for row in ticks})
        total = research.train_days + research.validation_days + research.oos_days
        grid = list(research.parameter_grid) or [self._parameter_row(self.base.auto)]
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
                candidates.append(
                    {
                        **parameters,
                        "score": 0.35 * self._score(train_metrics) + 0.65 * self._score(validation_metrics),
                        "max_drawdown": max(
                            abs(float(train_metrics.get("max_drawdown", 0.0))),
                            abs(float(validation_metrics.get("max_drawdown", 0.0))),
                        ),
                        "_train": train_metrics,
                        "_validation": validation_metrics,
                    }
                )

            selected = ParameterCalibrator().select_best(candidates)
            if selected is None:
                continue
            parameters = {
                key: selected[key]
                for key in (
                    "lookback",
                    "entry_z",
                    "exit_z",
                    "stop_z",
                    "min_net_edge",
                    "min_stationarity_score",
                    "max_half_life",
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
        final_config = self._config_with_parameters(selected_parameters)
        stress_results = {
            float(multiplier): self._run_portfolio(
                self._cost_stress(final_config, float(multiplier)), ticks
            )
            for multiplier in research.cost_stress_multipliers
        }
        robustness = self._robustness(final_config, ticks, folds, research)
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
        return {
            "leave_one_product_out": leave_one,
            "single_product": single,
            "remove_best_period": remove_best,
            "depth_haircut": depth,
            "latency": latency,
            "market_impact": impact,
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
            # Auto Universe 必须用该 fold 的历史交易日，而不是运行测试机器的自然日。
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
                return calculate_performance(
                    equity_curve,
                    initial_capital=config.initial_capital,
                    trade_count=len(broker.get_trades()),
                )
            finally:
                engine.stop()

    def _config_with_parameters(self, parameters: dict):
        allowed = {
            key: value
            for key, value in parameters.items()
            if key in self.base.auto.__dataclass_fields__
        }
        return replace(self.base, auto=replace(self.base.auto, **allowed))

    @staticmethod
    def _parameter_row(auto) -> dict:
        return {
            "lookback": auto.lookback,
            "entry_z": auto.entry_z,
            "exit_z": auto.exit_z,
            "stop_z": auto.stop_z,
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
            # 保持合法配置但使用不存在的产品，从而得到真实“删除唯一品种”空组合结果。
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
        }

    @staticmethod
    def _validate(config: AutoPortfolioResearchConfig) -> None:
        if any(value <= 0 for value in (config.train_days, config.validation_days, config.oos_days, config.step_days)):
            raise ValueError("auto walk-forward windows must be positive")
        if not config.cost_stress_multipliers or any(value <= 0 for value in config.cost_stress_multipliers):
            raise ValueError("cost stress multipliers must be positive")
        if any(not 0 < value <= 1 for value in config.depth_haircuts):
            raise ValueError("depth haircuts must be within (0, 1]")
        if any(value < 0 for value in config.extra_latency_ticks + config.extra_impact_ticks):
            raise ValueError("latency/impact stress cannot be negative")
