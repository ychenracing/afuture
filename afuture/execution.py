"""双腿套利执行器。

CTP 不提供跨合约原子成交，因此使用 FAK 限价单降低挂单裸腿时间；
任何一腿提交失败都撤销尚未成交的订单，已成交裸腿只允许反向减仓。
"""

from __future__ import annotations

from dataclasses import replace
from time import monotonic

from .models import (
    ContractSpec,
    ExecutionResult,
    Offset,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PairConfig,
    SignalAction,
    SpreadSignal,
    Tick,
)
from .position import PositionBook
from .risk import OrderRateLimiter, RiskManager


class PairExecutor:
    """把策略意图转换为双腿订单，并执行组合级事前风控。"""

    def __init__(
        self,
        broker,
        risk_manager: RiskManager,
        specs: dict[str, ContractSpec],
        *,
        aggressive_ticks: int = 1,
        close_today_first: bool = False,
    ) -> None:
        self.broker = broker
        self.risk_manager = risk_manager
        self.specs = specs
        self.aggressive_ticks = max(0, aggressive_ticks)
        self.close_today_first = close_today_first
        self.rate_limiter = OrderRateLimiter(risk_manager.config.max_orders_per_minute)

    def execute_signal(
        self,
        pair: PairConfig,
        signal: SpreadSignal,
        near: Tick,
        far: Tick,
        *,
        open_pair_count: int,
    ) -> ExecutionResult:
        if signal.action is SignalAction.HOLD:
            return ExecutionResult(False, reason="hold signal")
        if not self.broker.is_ready():
            return ExecutionResult(False, reason="broker is not ready")

        quote_decision = self.risk_manager.check_quotes([near, far], signal.timestamp)
        if not quote_decision.allowed:
            return ExecutionResult(False, reason=quote_decision.reason)

        opening = signal.action in {SignalAction.LONG_SPREAD, SignalAction.SHORT_SPREAD}
        calendar_decision = self.risk_manager.check_pair_calendar(pair, signal.timestamp, opening=opening)
        if not calendar_decision.allowed:
            return ExecutionResult(False, reason=calendar_decision.reason)

        if opening:
            requests = self._build_open_orders(pair, signal.action, near, far)
            current_volumes = {
                p.symbol: p.long_total + p.short_total for p in self.broker.get_positions()
            }
            decision = self.risk_manager.check_open_batch(
                self.broker.get_account(),
                requests,
                self.specs,
                open_pair_count=open_pair_count,
                current_contract_volumes=current_volumes,
            )
            if not decision.allowed:
                return ExecutionResult(False, reason=decision.reason)
        else:
            requests = self._build_close_orders(pair, near, far)
            if not requests:
                return ExecutionResult(False, reason="pair has no matching position to close")

        order_ids: list[str] = []
        try:
            for request in requests:
                if not self.rate_limiter.allow(monotonic()):
                    raise RuntimeError("order rate limit reached")
                order_ids.append(self.broker.send_order(request))
        except Exception as exc:
            self._rollback_submitted(order_ids, near, far)
            return ExecutionResult(False, tuple(order_ids), f"pair submission failed: {exc}")
        return ExecutionResult(True, tuple(order_ids))

    def pair_is_balanced(self, pair: PairConfig) -> bool:
        """没有活动组合委托时，双腿持仓必须等量且方向相反。"""
        active = [
            order for order in self.broker.get_active_orders()
            if order.request.reference.startswith(pair.pair_id)
        ]
        if active:
            return True
        book = PositionBook(self.broker.get_positions())
        near = book.get(pair.near_symbol, pair.exchange)
        far = book.get(pair.far_symbol, pair.exchange)
        long_spread = (
            near.long_total == far.short_total
            and near.short_total == 0
            and far.long_total == 0
        )
        short_spread = (
            near.short_total == far.long_total
            and near.long_total == 0
            and far.short_total == 0
        )
        return (near.empty and far.empty) or long_spread or short_spread

    def flatten_imbalance(self, pair: PairConfig, near: Tick, far: Tick) -> list[str]:
        """发生裸腿后只减仓；紧急减仓不受普通报单速率限制阻断。"""
        book = PositionBook(self.broker.get_positions())
        order_ids: list[str] = []
        for symbol, tick in ((pair.near_symbol, near), (pair.far_symbol, far)):
            position = book.get(symbol, pair.exchange)
            for side, volume in (
                (OrderSide.SELL, position.long_total),
                (OrderSide.BUY, position.short_total),
            ):
                if volume <= 0:
                    continue
                price = self._aggressive_price(tick, side)
                for child in book.plan_close(
                    symbol,
                    pair.exchange,
                    side,
                    volume,
                    close_today_first=self.close_today_first,
                    price=price,
                    reference=f"{pair.pair_id}:repair",
                ):
                    order_ids.append(
                        self.broker.send_order(replace(child, order_type=OrderType.FAK))
                    )
        return order_ids

    def _build_open_orders(
        self, pair: PairConfig, action: SignalAction, near: Tick, far: Tick
    ) -> list[OrderRequest]:
        sides = (
            (OrderSide.BUY, OrderSide.SELL)
            if action is SignalAction.LONG_SPREAD
            else (OrderSide.SELL, OrderSide.BUY)
        )
        return [
            OrderRequest(
                pair.near_symbol,
                pair.exchange,
                sides[0],
                Offset.OPEN,
                pair.volume,
                self._aggressive_price(near, sides[0]),
                OrderType.FAK,
                pair.pair_id,
            ),
            OrderRequest(
                pair.far_symbol,
                pair.exchange,
                sides[1],
                Offset.OPEN,
                pair.volume,
                self._aggressive_price(far, sides[1]),
                OrderType.FAK,
                pair.pair_id,
            ),
        ]

    def _build_close_orders(
        self, pair: PairConfig, near: Tick, far: Tick
    ) -> list[OrderRequest]:
        book = PositionBook(self.broker.get_positions())
        near_position = book.get(pair.near_symbol, pair.exchange)
        far_position = book.get(pair.far_symbol, pair.exchange)
        legs: list[tuple[str, Tick, OrderSide, int]] = []
        if near_position.long_total and far_position.short_total:
            volume = min(near_position.long_total, far_position.short_total)
            legs = [
                (pair.near_symbol, near, OrderSide.SELL, volume),
                (pair.far_symbol, far, OrderSide.BUY, volume),
            ]
        elif near_position.short_total and far_position.long_total:
            volume = min(near_position.short_total, far_position.long_total)
            legs = [
                (pair.near_symbol, near, OrderSide.BUY, volume),
                (pair.far_symbol, far, OrderSide.SELL, volume),
            ]

        requests: list[OrderRequest] = []
        for symbol, tick, side, volume in legs:
            children = book.plan_close(
                symbol,
                pair.exchange,
                side,
                volume,
                close_today_first=self.close_today_first,
                price=self._aggressive_price(tick, side),
                reference=pair.pair_id,
            )
            requests.extend(replace(child, order_type=OrderType.FAK) for child in children)
        return requests

    def _aggressive_price(self, tick: Tick, side: OrderSide) -> float:
        spec = self.specs[tick.symbol]
        if side is OrderSide.BUY:
            price = tick.ask_price + self.aggressive_ticks * spec.price_tick
            return min(price, tick.limit_up) if tick.limit_up > 0 else price
        price = tick.bid_price - self.aggressive_ticks * spec.price_tick
        return max(price, tick.limit_down) if tick.limit_down > 0 else price

    def _rollback_submitted(self, order_ids: list[str], near: Tick, far: Tick) -> None:
        for order_id in order_ids:
            self.broker.cancel_order(order_id)

        getter = getattr(self.broker, "get_order", None)
        if not callable(getter):
            return
        tick_map = {near.symbol: near, far.symbol: far}
        book = PositionBook(self.broker.get_positions())
        for order_id in order_ids:
            order = getter(order_id)
            if (
                order is None
                or order.traded <= 0
                or order.request.offset is not Offset.OPEN
            ):
                continue
            side = OrderSide.SELL if order.request.side is OrderSide.BUY else OrderSide.BUY
            position = book.get(order.request.symbol, order.request.exchange)
            available = position.long_total if side is OrderSide.SELL else position.short_total
            volume = min(order.traded, available)
            if volume <= 0:
                continue
            for child in book.plan_close(
                order.request.symbol,
                order.request.exchange,
                side,
                volume,
                close_today_first=self.close_today_first,
                price=self._aggressive_price(tick_map[order.request.symbol], side),
                reference=f"{order.request.reference}:rollback",
            ):
                try:
                    self.broker.send_order(replace(child, order_type=OrderType.FAK))
                except Exception:
                    # 已进入异常恢复路径；后续由引擎的失衡审计触发持久化停机。
                    pass
