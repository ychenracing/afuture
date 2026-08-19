"""确定性模拟柜台，用于回放、测试和实盘前演练。"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

from .base import Broker
from ..fees import calculate_commission
from ..models import (
    AccountSnapshot,
    BrokerEvent,
    ContractPosition,
    ContractSpec,
    Offset,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Tick,
    Trade,
)
from ..position import PositionBook


class SimBroker(Broker):
    """按一档盘口撮合，支持可成交限价单、FAK 和简单部分成交。"""

    def __init__(
        self,
        initial_capital: float,
        specs: dict[str, ContractSpec],
        *,
        slippage_ticks: int = 0,
    ) -> None:
        self.initial_capital = initial_capital
        self.specs = specs
        self.slippage_ticks = max(0, slippage_ticks)
        self.position_book = PositionBook()
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._ticks: dict[str, Tick] = {}
        self._events: list[BrokerEvent] = []
        self._order_seq = count(1)
        self._trade_seq = count(1)
        self._started = False
        self._balance = initial_capital
        self._realized_pnl = 0.0
        self._commission = 0.0
        self._trading_day = ""

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def is_ready(self) -> bool:
        return self._started

    def subscribe(self, symbol: str, exchange: str) -> None:
        if symbol not in self.specs:
            raise KeyError(f"unknown contract: {symbol}")

    def publish_tick(self, tick: Tick) -> None:
        tick.validate()
        if self._trading_day and tick.trading_day != self._trading_day:
            self.position_book.roll_trading_day()
        self._trading_day = tick.trading_day
        self._ticks[tick.symbol] = tick
        self._events.append(BrokerEvent("tick", tick))
        self._match_symbol(tick.symbol)

    def send_order(self, request: OrderRequest) -> str:
        if not self._started:
            raise RuntimeError("sim broker is not started")
        if request.symbol not in self.specs:
            raise KeyError(f"unknown contract: {request.symbol}")
        if request.volume <= 0 or request.price <= 0:
            raise ValueError("invalid order request")
        order_id = f"SIM-{next(self._order_seq)}"
        order = Order(order_id=order_id, request=request, status=OrderStatus.NOT_TRADED)
        self._orders[order_id] = order
        self._events.append(BrokerEvent("order", order))
        self._match_order(order)
        if request.order_type in {OrderType.FAK, OrderType.FOK} and order.active:
            order.status = OrderStatus.CANCELLED
            self._events.append(BrokerEvent("order", order))
        return order_id

    def cancel_order(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order and order.active:
            order.status = OrderStatus.CANCELLED
            self._events.append(BrokerEvent("order", order))

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[Order]:
        return [order for order in self._orders.values() if order.active]

    def get_trades(self) -> list[Trade]:
        """返回成交历史副本，供回放统计使用。"""
        return list(self._trades)

    def get_orders(self) -> list[Order]:
        """返回订单历史副本。"""
        return list(self._orders.values())

    def get_positions(self) -> list[ContractPosition]:
        return self.position_book.all()

    def get_account(self) -> AccountSnapshot:
        unrealized = 0.0
        margin = 0.0
        for p in self.position_book.all():
            spec = self.specs[p.symbol]
            tick = self._ticks.get(p.symbol)
            mark = tick.last_price if tick else max(p.long_price, p.short_price, 0.0)
            unrealized += (mark - p.long_price) * p.long_total * spec.multiplier
            unrealized += (p.short_price - mark) * p.short_total * spec.multiplier
            margin += mark * spec.multiplier * (
                p.long_total * spec.margin_rate_long + p.short_total * spec.margin_rate_short
            )
        equity = self._balance + unrealized
        return AccountSnapshot(
            balance=self._balance,
            equity=equity,
            available=equity - margin,
            margin=margin,
            realized_pnl=self._realized_pnl - self._commission,
            unrealized_pnl=unrealized,
            trading_day=self._trading_day or datetime.now(timezone.utc).strftime("%Y%m%d"),
        )

    def poll_events(self) -> list[BrokerEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def _match_symbol(self, symbol: str) -> None:
        for order in list(self._orders.values()):
            if order.active and order.request.symbol == symbol:
                self._match_order(order)

    def _match_order(self, order: Order) -> None:
        tick = self._ticks.get(order.request.symbol)
        if tick is None or not order.active:
            return
        request = order.request
        if request.side is OrderSide.BUY:
            marketable = request.price >= tick.ask_price
            available = int(tick.ask_volume)
            raw_price = tick.ask_price
            signed_slip = 1
        else:
            marketable = request.price <= tick.bid_price
            available = int(tick.bid_volume)
            raw_price = tick.bid_price
            signed_slip = -1
        if not marketable or available <= 0:
            return

        remaining = request.volume - order.traded
        if request.order_type is OrderType.FOK and available < remaining:
            return
        fill_volume = min(remaining, available)
        spec = self.specs[request.symbol]
        fill_price = raw_price + signed_slip * self.slippage_ticks * spec.price_tick
        self._fill(order, fill_volume, fill_price)

    def _fill(self, order: Order, volume: int, price: float) -> None:
        previous_traded = order.traded
        order.traded += volume
        order.average_price = (
            (order.average_price * previous_traded + price * volume) / order.traded
        )
        order.status = OrderStatus.FILLED if order.traded == order.request.volume else OrderStatus.PART_TRADED

        trade = Trade(
            trade_id=f"SIM-T-{next(self._trade_seq)}",
            order_id=order.order_id,
            symbol=order.request.symbol,
            exchange=order.request.exchange,
            side=order.request.side,
            offset=order.request.offset,
            volume=volume,
            price=price,
            timestamp=self._ticks[order.request.symbol].timestamp,
        )
        points = self.position_book.apply_trade(trade)
        spec = self.specs[trade.symbol]
        realized = points * spec.multiplier
        commission = calculate_commission(spec, trade.offset, trade.price, trade.volume)
        self._realized_pnl += realized
        self._commission += commission
        self._balance += realized - commission
        trade = Trade(**{**trade.__dict__, "commission": commission})
        self._trades.append(trade)
        self._events.append(BrokerEvent("trade", trade))
        self._events.append(BrokerEvent("order", order))
