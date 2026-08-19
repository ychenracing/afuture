"""账户级和组合级事前风险控制。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (
    AccountSnapshot,
    ContractSpec,
    Offset,
    OrderRequest,
    OrderSide,
    PairConfig,
    RiskDecision,
    Tick,
)


@dataclass(frozen=True)
class RiskConfig:
    """默认参数偏保守，目的是先限制失控风险，再讨论收益。"""

    max_margin_ratio: float = 0.35
    max_daily_loss_ratio: float = 0.01
    max_total_drawdown_ratio: float = 0.08
    max_open_pairs: int = 3
    max_contract_volume: int = 10
    max_quote_age_seconds: float = 10.0
    expiry_blackout_days: int = 5
    min_available_ratio: float = 0.50
    margin_estimate_buffer: float = 1.20
    max_orders_per_minute: int = 20


class OrderRateLimiter:
    """限制单位时间内报单次数，避免策略异常形成报单风暴。"""

    def __init__(self, max_orders_per_minute: int) -> None:
        if max_orders_per_minute <= 0:
            raise ValueError("max_orders_per_minute must be positive")
        self.max_orders_per_minute = max_orders_per_minute
        self._timestamps: deque[float] = deque()

    def allow(self, now_monotonic: float) -> bool:
        cutoff = now_monotonic - 60.0
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_orders_per_minute:
            return False
        self._timestamps.append(now_monotonic)
        return True


class RiskManager:
    """风控只决定是否允许交易，不负责挑选套利机会。"""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._validate_config()
        self._day_start_equity: float | None = None
        self._trading_day = ""
        self._high_watermark: float | None = None

    @property
    def high_watermark(self) -> float:
        return self._high_watermark or 0.0

    def restore_high_watermark(self, equity: float) -> None:
        """从持久化状态恢复账户权益高水位，避免重启绕过总回撤限制。"""
        if equity > 0:
            self._high_watermark = max(self._high_watermark or 0.0, equity)

    def set_day_start_equity(self, equity: float, trading_day: str) -> None:
        if equity <= 0:
            raise ValueError("equity must be positive")
        self._day_start_equity = equity
        self._trading_day = trading_day
        self._high_watermark = max(self._high_watermark or equity, equity)

    def check_account(self, account: AccountSnapshot) -> RiskDecision:
        if account.equity <= 0:
            return RiskDecision(False, "equity is not positive")
        if self._day_start_equity is None or self._trading_day != account.trading_day:
            self.set_day_start_equity(account.equity, account.trading_day)

        assert self._day_start_equity is not None
        self._high_watermark = max(self._high_watermark or account.equity, account.equity)
        daily_loss = max(0.0, self._day_start_equity - account.equity) / self._day_start_equity
        drawdown = max(0.0, self._high_watermark - account.equity) / self._high_watermark
        margin_ratio = account.margin / account.equity
        available_ratio = account.available / account.equity

        if daily_loss >= self.config.max_daily_loss_ratio:
            return RiskDecision(False, "daily loss limit reached")
        if drawdown >= self.config.max_total_drawdown_ratio:
            return RiskDecision(False, "drawdown limit reached")
        if margin_ratio > self.config.max_margin_ratio:
            return RiskDecision(False, "margin ratio limit reached")
        if available_ratio < self.config.min_available_ratio:
            return RiskDecision(False, "available cash reserve too low")
        return RiskDecision(True)

    def check_quotes(self, ticks: list[Tick], now: datetime | None = None) -> RiskDecision:
        if not ticks:
            return RiskDecision(False, "missing quotes")
        now = now or datetime.now(timezone.utc)
        trading_days = {tick.trading_day for tick in ticks}
        if len(trading_days) != 1:
            return RiskDecision(False, "quotes belong to different trading days")
        for tick in ticks:
            try:
                tick.validate()
            except ValueError as exc:
                return RiskDecision(False, f"invalid quote: {exc}")
            age = (now - tick.timestamp.astimezone(timezone.utc)).total_seconds()
            if age < -2:
                return RiskDecision(False, "quote timestamp is in the future")
            if age > self.config.max_quote_age_seconds:
                return RiskDecision(False, "stale quote")
        return RiskDecision(True)

    def check_pair_calendar(self, pair: PairConfig, now: datetime, *, opening: bool) -> RiskDecision:
        if not opening:
            return RiskDecision(True)
        for raw in (pair.expiry_near, pair.expiry_far):
            if not raw:
                continue
            expiry = datetime.fromisoformat(raw).date()
            days = (expiry - now.date()).days
            if days <= self.config.expiry_blackout_days:
                return RiskDecision(False, "contract is inside expiry blackout window")
        return RiskDecision(True)

    def check_open_batch(
        self,
        account: AccountSnapshot,
        orders: list[OrderRequest],
        specs: dict[str, ContractSpec],
        *,
        open_pair_count: int,
        current_contract_volumes: dict[str, int] | None = None,
    ) -> RiskDecision:
        """一次性校验整个套利组合，避免两腿分别通过却合计超限。"""
        account_decision = self.check_account(account)
        if not account_decision.allowed:
            return account_decision
        if open_pair_count >= self.config.max_open_pairs:
            return RiskDecision(False, "open pair limit reached")

        current_contract_volumes = current_contract_volumes or {}
        requested_by_symbol: dict[str, int] = {}
        estimated_margin = 0.0
        for order in orders:
            if order.offset is not Offset.OPEN:
                continue
            if order.volume <= 0:
                return RiskDecision(False, "contract volume must be positive")
            requested_by_symbol[order.symbol] = requested_by_symbol.get(order.symbol, 0) + order.volume
            if current_contract_volumes.get(order.symbol, 0) + requested_by_symbol[order.symbol] > self.config.max_contract_volume:
                return RiskDecision(False, "contract volume limit reached")
            spec = specs.get(order.symbol)
            if spec is None:
                return RiskDecision(False, f"missing contract spec: {order.symbol}")
            rate = spec.margin_rate_long if order.side is OrderSide.BUY else spec.margin_rate_short
            estimated_margin += order.price * spec.multiplier * rate * order.volume

        estimated_margin *= self.config.margin_estimate_buffer
        post_margin = account.margin + estimated_margin
        if post_margin / account.equity > self.config.max_margin_ratio:
            return RiskDecision(False, "combined margin ratio would exceed limit")
        if (account.equity - post_margin) / account.equity < self.config.min_available_ratio:
            return RiskDecision(False, "combined cash reserve would fall below limit")
        return RiskDecision(True)

    def _validate_config(self) -> None:
        ratios = (
            self.config.max_margin_ratio,
            self.config.max_daily_loss_ratio,
            self.config.max_total_drawdown_ratio,
            self.config.min_available_ratio,
        )
        if any(value <= 0 or value >= 1 for value in ratios):
            raise ValueError("risk ratios must be between 0 and 1")
        if self.config.margin_estimate_buffer < 1:
            raise ValueError("margin_estimate_buffer must be at least 1")
        if self.config.max_open_pairs <= 0 or self.config.max_contract_volume <= 0:
            raise ValueError("position limits must be positive")
        if self.config.expiry_blackout_days < 0:
            raise ValueError("expiry blackout cannot be negative")
        if self.config.max_quote_age_seconds <= 0 or self.config.max_orders_per_minute <= 0:
            raise ValueError("time and order-rate limits must be positive")
