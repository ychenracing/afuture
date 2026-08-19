"""持仓簿和交易所平今/平昨拆单规则。"""

from __future__ import annotations

from dataclasses import replace

from .models import ContractPosition, Offset, OrderRequest, OrderSide, Trade


_SPECIAL_CLOSE_EXCHANGES = {"SHFE", "INE"}


class PositionBook:
    """维护今昨、多空持仓以及开仓均价。"""

    def __init__(self, positions: list[ContractPosition] | None = None) -> None:
        self._positions: dict[str, ContractPosition] = {
            p.symbol: replace(p) for p in (positions or []) if not p.empty
        }

    def get(self, symbol: str, exchange: str = "") -> ContractPosition:
        position = self._positions.get(symbol)
        if position is None:
            position = ContractPosition(symbol=symbol, exchange=exchange)
            self._positions[symbol] = position
        return position

    def all(self) -> list[ContractPosition]:
        return [replace(p) for p in self._positions.values() if not p.empty]

    def roll_trading_day(self) -> None:
        """进入新交易日后把今仓转为昨仓。"""
        for position in self._positions.values():
            position.long_yesterday += position.long_today
            position.long_today = 0
            position.short_yesterday += position.short_today
            position.short_today = 0

    def apply_trade(self, trade: Trade) -> float:
        """应用成交并返回以价格点计的已实现盈亏，不含合约乘数。"""
        if trade.volume <= 0:
            raise ValueError("trade volume must be positive")
        p = self.get(trade.symbol, trade.exchange)
        if trade.offset is Offset.OPEN:
            if trade.side is OrderSide.BUY:
                total = p.long_total + trade.volume
                p.long_price = ((p.long_price * p.long_total) + trade.price * trade.volume) / total
                p.long_today += trade.volume
            else:
                total = p.short_total + trade.volume
                p.short_price = ((p.short_price * p.short_total) + trade.price * trade.volume) / total
                p.short_today += trade.volume
            return 0.0

        if trade.side is OrderSide.SELL:
            if trade.volume > p.long_total:
                raise ValueError("close volume exceeds long position")
            realized = (trade.price - p.long_price) * trade.volume
            self._consume_long(p, trade.offset, trade.volume)
            if p.long_total == 0:
                p.long_price = 0.0
        else:
            if trade.volume > p.short_total:
                raise ValueError("close volume exceeds short position")
            realized = (p.short_price - trade.price) * trade.volume
            self._consume_short(p, trade.offset, trade.volume)
            if p.short_total == 0:
                p.short_price = 0.0
        return realized

    def plan_close(
        self,
        symbol: str,
        exchange: str,
        side: OrderSide,
        volume: int,
        *,
        close_today_first: bool = False,
        price: float = 0.0,
        reference: str = "",
    ) -> list[OrderRequest]:
        """按交易所规则把平仓数量拆成可报送的子订单。"""
        if volume <= 0:
            raise ValueError("close volume must be positive")
        p = self.get(symbol, exchange)
        today, yesterday = (
            (p.long_today, p.long_yesterday)
            if side is OrderSide.SELL
            else (p.short_today, p.short_yesterday)
        )
        if volume > today + yesterday:
            raise ValueError("close volume exceeds position")

        if exchange not in _SPECIAL_CLOSE_EXCHANGES:
            return [OrderRequest(symbol, exchange, side, Offset.CLOSE, volume, price, reference=reference)]

        plan: list[OrderRequest] = []
        remaining = volume
        buckets = (
            [(Offset.CLOSE_TODAY, today), (Offset.CLOSE_YESTERDAY, yesterday)]
            if close_today_first
            else [(Offset.CLOSE_YESTERDAY, yesterday), (Offset.CLOSE_TODAY, today)]
        )
        for offset, available in buckets:
            if remaining <= 0:
                break
            child_volume = min(remaining, available)
            if child_volume:
                plan.append(OrderRequest(symbol, exchange, side, offset, child_volume, price, reference=reference))
                remaining -= child_volume
        return plan

    @staticmethod
    def _consume_long(p: ContractPosition, offset: Offset, volume: int) -> None:
        if offset is Offset.CLOSE_TODAY:
            p.long_today -= volume
            return
        if offset is Offset.CLOSE_YESTERDAY:
            p.long_yesterday -= volume
            return
        from_yesterday = min(volume, p.long_yesterday)
        p.long_yesterday -= from_yesterday
        p.long_today -= volume - from_yesterday

    @staticmethod
    def _consume_short(p: ContractPosition, offset: Offset, volume: int) -> None:
        if offset is Offset.CLOSE_TODAY:
            p.short_today -= volume
            return
        if offset is Offset.CLOSE_YESTERDAY:
            p.short_yesterday -= volume
            return
        from_yesterday = min(volume, p.short_yesterday)
        p.short_yesterday -= from_yesterday
        p.short_today -= volume - from_yesterday
