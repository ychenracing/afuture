from datetime import datetime, timezone

from afuture.models import Offset, OrderSide, Trade
from afuture.position import PositionBook


def trade(symbol: str, side: OrderSide, offset: Offset, volume: int, price: float) -> Trade:
    return Trade(
        trade_id=f"t-{symbol}-{side.value}-{offset.value}-{volume}",
        order_id="o1",
        symbol=symbol,
        exchange="SHFE",
        side=side,
        offset=offset,
        volume=volume,
        price=price,
        timestamp=datetime.now(timezone.utc),
    )


def test_shfe_close_plan_splits_yesterday_and_today():
    book = PositionBook()
    book.apply_trade(trade("rb2610", OrderSide.BUY, Offset.OPEN, 3, 3500))
    book.roll_trading_day()
    book.apply_trade(trade("rb2610", OrderSide.BUY, Offset.OPEN, 2, 3510))

    plan = book.plan_close("rb2610", "SHFE", OrderSide.SELL, 4, close_today_first=False)
    assert [(p.offset, p.volume) for p in plan] == [
        (Offset.CLOSE_YESTERDAY, 3),
        (Offset.CLOSE_TODAY, 1),
    ]


def test_non_shfe_close_plan_uses_generic_close():
    book = PositionBook()
    book.apply_trade(trade("m2609", OrderSide.BUY, Offset.OPEN, 2, 3000))
    plan = book.plan_close("m2609", "DCE", OrderSide.SELL, 2)
    assert len(plan) == 1
    assert plan[0].offset is Offset.CLOSE
    assert plan[0].volume == 2


def test_apply_close_reduces_position_and_returns_realized_points():
    book = PositionBook()
    book.apply_trade(trade("rb2610", OrderSide.BUY, Offset.OPEN, 2, 3500))
    realized = book.apply_trade(trade("rb2610", OrderSide.SELL, Offset.CLOSE_TODAY, 1, 3520))
    position = book.get("rb2610")
    assert position.long_today == 1
    assert realized == 20
