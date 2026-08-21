"""期限结构策略族的轻量、因果研究筛选。

本模块只用于在完整生产回放之前快速淘汰没有经济信号的策略家族。它重建每日
F1/F2 角色序列，所有信号只使用当前及历史已观察行情，并从下一持有区间开始记
收益；换月日不跨合约拼接收益。筛选通过后仍必须进入 TradingEngine 完整回放。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import log, sqrt
from statistics import mean

from .models import ContractInfo, ContractSpec, Tick


@dataclass(frozen=True)
class CurveFamilyConfig:
    """少量预注册期限结构策略族参数。"""

    family: str
    fast_window: int = 5
    slow_window: int = 60
    mean_window: int = 30
    entry_z: float = 1.5
    exit_z: float = 0.5
    change_severity: float = 1.5
    min_volatility_percentile: float = 0.0
    rebalance_samples: int = 5
    slippage_ticks: int = 1


@dataclass(frozen=True)
class CurveObservation:
    """某交易日真实可见的近月/次近月角色价格与连续角色指数。"""

    trading_day: str
    near_symbol: str
    far_symbol: str
    near_price: float
    far_price: float
    near_role_index: float | None = None
    far_role_index: float | None = None


class CurveFamilyResearch:
    """用动态 F1/F2 角色序列快速筛选期限结构 Alpha。"""

    SUPPORTED_FAMILIES = {
        "log_ratio_mean_reversion",
        "basis_reversal",
        "basis_momentum",
        "slow_momentum_fast_reversion",
    }

    def __init__(
        self,
        ticks: list[Tick],
        catalog: list[ContractInfo],
        specs: dict[str, ContractSpec],
        *,
        min_days_to_expiry: int = 20,
    ) -> None:
        self.specs = specs
        self.min_days_to_expiry = max(0, int(min_days_to_expiry))
        self._catalog = list(catalog)
        self._series = self._build_role_series(ticks)

    @property
    def products(self) -> tuple[str, ...]:
        return tuple(sorted(self._series))

    def run(
        self,
        config: CurveFamilyConfig,
        allowed_days: set[str] | None = None,
    ) -> dict:
        """返回等权产品组合的成本后研究指标。"""
        self._validate(config)
        product_returns: dict[str, dict[str, float]] = {}
        product_trades: dict[str, int] = {}
        all_days: set[str] = set()
        for product, observations in self._series.items():
            returns, trades = self._run_product(
                observations,
                config,
                allowed_days,
            )
            product_returns[product] = returns
            product_trades[product] = trades
            all_days.update(returns)

        daily: list[float] = []
        for trading_day in sorted(all_days):
            rows = [
                values[trading_day]
                for values in product_returns.values()
                if trading_day in values
            ]
            if rows:
                daily.append(sum(rows) / len(rows))

        metrics = self._metrics(daily)
        metrics["trading_days"] = len(daily)
        metrics["trade_count"] = sum(product_trades.values())
        metrics["product_total_returns"] = {
            product: self._compound(list(values.values()))
            for product, values in product_returns.items()
        }
        product_totals = metrics["product_total_returns"]
        metrics["positive_product_ratio"] = (
            sum(value > 0 for value in product_totals.values())
            / len(product_totals)
            if product_totals
            else 0.0
        )
        return metrics

    def _run_product(
        self,
        observations: list[CurveObservation],
        config: CurveFamilyConfig,
        allowed_days: set[str] | None,
    ) -> tuple[dict[str, float], int]:
        """按日先结算旧仓，再用当前信息决定下一持有区间。

        研究边界、F1/F2 换月和样本结束都必须显式关闭已有仓位并扣成本；不能用
        ``position = 0`` 免费丢弃真实交易中必须发生的退出。
        """
        returns: dict[str, float] = {}
        position = 0
        trades = 0
        last_rebalance = -config.rebalance_samples

        for index in range(1, len(observations)):
            previous = observations[index - 1]
            current = observations[index]
            previous_allowed = (
                allowed_days is None or previous.trading_day in allowed_days
            )
            current_allowed = (
                allowed_days is None or current.trading_day in allowed_days
            )

            if not current_allowed:
                if position != 0 and previous_allowed:
                    trades += self._charge_exit(
                        returns,
                        previous,
                        position,
                        config.slippage_ticks,
                    )
                position = 0
                continue
            if not previous_allowed:
                # 不把研究窗口外的持仓/PnL 带进当前窗口；从当前日重新开始决策。
                position = 0
                last_rebalance = index - config.rebalance_samples

            same_pair = (
                previous.near_symbol == current.near_symbol
                and previous.far_symbol == current.far_symbol
            )
            if not same_pair:
                # F1/F2 角色切换时不计不同合约的价格跳变，但已有旧 pair 必须按
                # 换月前最后可见价付出一次退出成本。
                if position != 0 and previous_allowed:
                    trades += self._charge_exit(
                        returns,
                        previous,
                        position,
                        config.slippage_ticks,
                    )
                position = 0
                last_rebalance = index
                continue

            gross_return = position * self._equal_lot_return(previous, current)

            desired = position
            if index - last_rebalance >= max(1, config.rebalance_samples):
                desired = self._desired_position(
                    observations,
                    index,
                    position,
                    config,
                )
                last_rebalance = index
            turnover = abs(desired - position)
            cost = self._transaction_cost(
                current,
                turnover,
                config.slippage_ticks,
            )
            returns[current.trading_day] = gross_return - cost
            if desired != position:
                trades += turnover
            position = desired

        if position != 0 and observations:
            final = observations[-1]
            if allowed_days is None or final.trading_day in allowed_days:
                trades += self._charge_exit(
                    returns,
                    final,
                    position,
                    config.slippage_ticks,
                )
        return returns, trades

    def _charge_exit(
        self,
        returns: dict[str, float],
        observation: CurveObservation,
        position: int,
        slippage_ticks: int,
    ) -> int:
        """在最后可成交观察点扣除关闭现有 spread 的单边交易成本。"""
        turnover = abs(int(position))
        if turnover <= 0:
            return 0
        cost = self._transaction_cost(
            observation,
            turnover,
            slippage_ticks,
        )
        if cost:
            returns[observation.trading_day] = (
                returns.get(observation.trading_day, 0.0) - cost
            )
        return turnover

    def _equal_lot_return(
        self,
        previous: CurveObservation,
        current: CurveObservation,
    ) -> float:
        """按生产等手数口径计算一手多近空远的组合收益率。

        信号可以使用角色收益差，但真实 PnL 必须按合约乘数和价格变动计算；再用
        上一观察点两腿总名义金额归一化，避免把等手数执行误写成等名义百分比仓位。
        无 specs 的纯信号单测保留旧的收益差近似。
        """
        near_spec = self.specs.get(previous.near_symbol) if self.specs else None
        far_spec = self.specs.get(previous.far_symbol) if self.specs else None
        if near_spec is None or far_spec is None:
            near_return = current.near_price / previous.near_price - 1.0
            far_return = current.far_price / previous.far_price - 1.0
            return near_return - far_return

        near_pnl = (
            current.near_price - previous.near_price
        ) * near_spec.multiplier
        far_pnl = (
            current.far_price - previous.far_price
        ) * far_spec.multiplier
        gross_notional = (
            previous.near_price * near_spec.multiplier
            + previous.far_price * far_spec.multiplier
        )
        return (near_pnl - far_pnl) / max(gross_notional, 1e-9)

    def _desired_position(
        self,
        observations: list[CurveObservation],
        index: int,
        current_position: int,
        config: CurveFamilyConfig,
    ) -> int:
        """只使用截至 index 的可见价格决定下一持有区间目标。"""
        required = max(
            config.mean_window,
            config.slow_window,
            config.fast_window,
        ) + 1
        start = max(0, index - required + 1)
        window = observations[start : index + 1]

        # 长周期 momentum 研究的是连续 F1/F2 角色收益，允许跨实际合约换月；
        # 均值回归和短期 reversal 仍要求同一实际 pair，避免把角色指数误当价差。
        role_adjusted = (
            config.family in {
                "basis_momentum",
                "slow_momentum_fast_reversion",
            }
            and self._has_role_indices(window)
        )
        if not role_adjusted and not self._same_pair(window):
            return 0

        relative_changes = self._relative_daily_changes(
            window,
            role_adjusted=role_adjusted,
        )
        volatility_percentile = self._volatility_percentile(
            relative_changes,
            config.fast_window,
        )
        # pairs-trading-egarch 的关键方向是“波动太低不部署资本”，而不只是避开
        # 极高波动。默认 0 保持关闭状态。
        if volatility_percentile < config.min_volatility_percentile:
            return 0

        if config.family == "basis_reversal":
            # Rossi 2025 的 spread 版本：最近 F1-F2 相对收益为正，则下一期反向。
            relative = self._relative_return(window, config.fast_window)
            return -self._sign(relative)

        if config.family == "basis_momentum":
            # Boons 2019 的 basis momentum 用连续 F1/F2 角色指数的长期收益差；
            # 这里只研究 spread-neutral 方向版本，不复制原论文的 outright 横截面仓位。
            relative = self._relative_return(
                window,
                config.slow_window,
                role_adjusted=role_adjusted,
            )
            return self._sign(relative)

        if config.family == "slow_momentum_fast_reversion":
            # 对 Wood et al. 的轻量代理：正常时跟随慢相对趋势；近期出现与慢趋势
            # 相反且足够大的局部断点时，暂时切换到快速方向，下一次再由慢趋势接管。
            slow = self._relative_return(
                window,
                config.slow_window,
                role_adjusted=role_adjusted,
            )
            fast = self._relative_return(
                window,
                config.fast_window,
                role_adjusted=role_adjusted,
            )
            recent = relative_changes[-max(config.fast_window * 4, 10) :]
            sigma = self._std(recent)
            severity = abs(fast) / max(
                sigma * sqrt(config.fast_window),
                1e-9,
            )
            slow_sign = self._sign(slow)
            fast_sign = self._sign(fast)
            if (
                severity >= config.change_severity
                and fast_sign != 0
                and slow_sign != 0
                and fast_sign != slow_sign
            ):
                return fast_sign
            return slow_sign

        # 归一化 log(F1/F2) 均值回归；该信号消除绝对价格尺度差异。
        ratios = [
            log(row.near_price / row.far_price)
            for row in window[-config.mean_window :]
        ]
        if len(ratios) < config.mean_window:
            return 0
        history = ratios[:-1]
        reference_mean = mean(history)
        reference_std = self._std(history)
        zscore = (ratios[-1] - reference_mean) / max(reference_std, 1e-9)
        if current_position == 0:
            if zscore >= config.entry_z:
                return -1
            if zscore <= -config.entry_z:
                return 1
            return 0
        if current_position > 0:
            if zscore >= -config.exit_z or zscore <= -4.0:
                return 0
        elif zscore <= config.exit_z or zscore >= 4.0:
            return 0
        return current_position

    def _build_role_series(
        self,
        ticks: list[Tick],
    ) -> dict[str, list[CurveObservation]]:
        """按历史 listing/expiry 和当日真实出现合约重建 F1/F2 及连续角色指数。"""
        by_day: dict[str, dict[str, Tick]] = {}
        for tick in ticks:
            current = by_day.setdefault(tick.trading_day, {})
            existing = current.get(tick.symbol)
            # 两年日线代理每个合约每日只有一个 open tick；若将来输入多条，研究
            # 使用最早可见样本，避免偷偷使用日内未来价格。
            if existing is None or tick.timestamp < existing.timestamp:
                current[tick.symbol] = tick

        catalog_by_product: dict[str, list[ContractInfo]] = {}
        for row in self._catalog:
            catalog_by_product.setdefault(row.product.lower(), []).append(row)
        result: dict[str, list[CurveObservation]] = {
            product: [] for product in catalog_by_product
        }
        role_levels: dict[str, list[float]] = {
            product: [1.0, 1.0] for product in catalog_by_product
        }
        previous_observed: dict[str, Tick] = {}
        previous_trading_day: str | None = None

        for trading_day in sorted(by_day):
            day = date(
                int(trading_day[:4]),
                int(trading_day[4:6]),
                int(trading_day[6:8]),
            )
            observed = by_day[trading_day]
            for product, rows in catalog_by_product.items():
                eligible: list[tuple[date, ContractInfo]] = []
                for row in rows:
                    if row.symbol not in observed:
                        continue
                    try:
                        expiry = date.fromisoformat(row.expiry)
                        listing = (
                            date.fromisoformat(row.listing)
                            if row.listing
                            else None
                        )
                    except ValueError:
                        continue
                    if listing is not None and listing > day:
                        continue
                    if (expiry - day).days < self.min_days_to_expiry:
                        continue
                    eligible.append((expiry, row))
                eligible.sort(key=lambda item: (item[0], item[1].symbol))
                if len(eligible) < 2:
                    continue
                near = observed[eligible[0][1].symbol]
                far = observed[eligible[1][1].symbol]

                levels = role_levels[product]
                previous_row = result[product][-1] if result[product] else None
                contiguous = (
                    previous_row is not None
                    and previous_trading_day is not None
                    and previous_row.trading_day == previous_trading_day
                )
                if contiguous:
                    previous_near = previous_observed.get(near.symbol)
                    previous_far = previous_observed.get(far.symbol)
                    if previous_near is not None and previous_near.mid_price > 0:
                        levels[0] *= near.mid_price / previous_near.mid_price
                    if previous_far is not None and previous_far.mid_price > 0:
                        levels[1] *= far.mid_price / previous_far.mid_price

                result[product].append(
                    CurveObservation(
                        trading_day=trading_day,
                        near_symbol=near.symbol,
                        far_symbol=far.symbol,
                        near_price=near.mid_price,
                        far_price=far.mid_price,
                        near_role_index=levels[0],
                        far_role_index=levels[1],
                    )
                )
            previous_observed = observed
            previous_trading_day = trading_day
        return result

    def _transaction_cost(
        self,
        current: CurveObservation,
        turnover: int,
        slippage_ticks: int,
    ) -> float:
        """用真实 tick/multiplier/fee 对每次两腿换仓做保守成本扣减。"""
        if turnover <= 0 or not self.specs:
            return 0.0
        near_spec = self.specs[current.near_symbol]
        far_spec = self.specs[current.far_symbol]
        near_notional = current.near_price * near_spec.multiplier
        far_notional = current.far_price * far_spec.multiplier
        gross_notional = max(near_notional + far_notional, 1e-9)
        slippage = slippage_ticks * (
            near_spec.price_tick * near_spec.multiplier
            + far_spec.price_tick * far_spec.multiplier
        ) / gross_notional
        # 单次 position unit 变化只发生一次双腿成交；开/平费率取平均，避免把
        # 完整 round trip 同时扣在单边换仓上，同时保留 fixed fee 影响。
        fee = 0.5 * (
            self._round_trip_fee_ratio(near_spec, near_notional)
            + self._round_trip_fee_ratio(far_spec, far_notional)
        )
        return turnover * (slippage + fee)

    @staticmethod
    def _round_trip_fee_ratio(
        spec: ContractSpec,
        notional: float,
    ) -> float:
        fee = spec.fee
        cash = fee.open_fixed + fee.close_fixed
        cash += notional * (fee.open_rate + fee.close_rate)
        return cash / max(notional, 1e-9)

    @staticmethod
    def _same_pair(rows: list[CurveObservation]) -> bool:
        if not rows:
            return False
        pair = (rows[-1].near_symbol, rows[-1].far_symbol)
        return all(
            (row.near_symbol, row.far_symbol) == pair
            for row in rows
        )

    @staticmethod
    def _has_role_indices(rows: list[CurveObservation]) -> bool:
        return bool(rows) and all(
            row.near_role_index is not None
            and row.far_role_index is not None
            and row.near_role_index > 0
            and row.far_role_index > 0
            for row in rows
        )

    @staticmethod
    def _prices(
        row: CurveObservation,
        *,
        role_adjusted: bool,
    ) -> tuple[float, float]:
        if (
            role_adjusted
            and row.near_role_index is not None
            and row.far_role_index is not None
        ):
            return float(row.near_role_index), float(row.far_role_index)
        return float(row.near_price), float(row.far_price)

    @classmethod
    def _relative_return(
        cls,
        rows: list[CurveObservation],
        samples: int,
        *,
        role_adjusted: bool = False,
    ) -> float:
        if len(rows) <= samples:
            return 0.0
        start = rows[-samples - 1]
        end = rows[-1]
        start_near, start_far = cls._prices(start, role_adjusted=role_adjusted)
        end_near, end_far = cls._prices(end, role_adjusted=role_adjusted)
        near_return = end_near / start_near - 1.0
        far_return = end_far / start_far - 1.0
        return near_return - far_return

    @classmethod
    def _relative_daily_changes(
        cls,
        rows: list[CurveObservation],
        *,
        role_adjusted: bool = False,
    ) -> list[float]:
        result: list[float] = []
        for previous, current in zip(rows, rows[1:]):
            previous_near, previous_far = cls._prices(
                previous,
                role_adjusted=role_adjusted,
            )
            current_near, current_far = cls._prices(
                current,
                role_adjusted=role_adjusted,
            )
            near_return = current_near / previous_near - 1.0
            far_return = current_far / previous_far - 1.0
            result.append(near_return - far_return)
        return result

    @classmethod
    def _volatility_percentile(
        cls,
        changes: list[float],
        fast_window: int,
    ) -> float:
        if len(changes) < max(fast_window * 3, 10):
            return 0.5
        width = max(2, fast_window)
        rolling: list[float] = []
        for end in range(width, len(changes) + 1):
            rolling.append(cls._std(changes[end - width : end]))
        current = rolling[-1]
        prior = rolling[:-1]
        return (
            sum(value <= current for value in prior) / len(prior)
            if prior
            else 0.5
        )

    @staticmethod
    def _sign(value: float) -> int:
        if value > 1e-12:
            return 1
        if value < -1e-12:
            return -1
        return 0

    @staticmethod
    def _std(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        reference_mean = sum(values) / len(values)
        return sqrt(
            sum(
                (value - reference_mean) ** 2
                for value in values
            )
            / len(values)
        )

    @staticmethod
    def _compound(values: list[float]) -> float:
        equity = 1.0
        for value in values:
            equity *= 1.0 + value
        return equity - 1.0

    @classmethod
    def _metrics(cls, daily: list[float]) -> dict:
        if not daily:
            return {
                "total_return": 0.0,
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
            }
        total_return = cls._compound(daily)
        annualized_return = (
            (1.0 + total_return) ** (252.0 / len(daily)) - 1.0
            if total_return > -1.0
            else -1.0
        )
        daily_mean = sum(daily) / len(daily)
        daily_std = cls._std(daily)
        sharpe = (
            daily_mean / daily_std * sqrt(252.0)
            if daily_std > 1e-12
            else 0.0
        )
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in daily:
            equity *= 1.0 + value
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity / peak - 1.0)
        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "max_drawdown": max_drawdown,
            "sharpe": sharpe,
        }

    @classmethod
    def _validate(cls, config: CurveFamilyConfig) -> None:
        if config.family not in cls.SUPPORTED_FAMILIES:
            raise ValueError(f"unsupported curve family: {config.family}")
        if config.fast_window < 2:
            raise ValueError("fast_window must be at least 2")
        if config.slow_window < config.fast_window:
            raise ValueError("slow_window must be >= fast_window")
        if config.mean_window < 3:
            raise ValueError("mean_window must be at least 3")
        if config.rebalance_samples <= 0:
            raise ValueError("rebalance_samples must be positive")
        if not 0 <= config.min_volatility_percentile <= 1:
            raise ValueError(
                "min_volatility_percentile must be within [0, 1]"
            )
        if config.change_severity <= 0:
            raise ValueError("change_severity must be positive")
        if not 0 <= config.exit_z < config.entry_z:
            raise ValueError("curve mean-reversion thresholds are invalid")
