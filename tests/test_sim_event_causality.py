from datetime import datetime, timezone

from afuture.broker.sim import SimBroker
from afuture.models import ContractSpec, Offset, OrderRequest, OrderSide, OrderType, Tick


def _tick(symbol: str, bid: float, ask: float) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
        bid_price=bid,
        ask_price=ask,
        last_price=(bid + ask) / 2,
        bid_volume=20,
        ask_volume=20,
        trading_day="20260821",
    )


def test_latency_fill_events_precede_tick_strategy_event():
    broker = SimBroker(
        500000,
        {"N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15)},
        conservative=True,
        latency_ticks=1,
    )
    broker.start()
    try:
        broker.publish_tick(_tick("N", 99, 100))
        broker.poll_events()
        broker.send_order(
            OrderRequest(
                "N",
                "DCE",
                OrderSide.BUY,
                Offset.OPEN,
                1,
                100,
                OrderType.FAK,
                "p",
            )
        )
        # Drop the submission acknowledgement; the next market tick makes the
        # delayed order eligible and must update expected trade state before
        # that same tick can drive a fresh strategy decision.
        broker.poll_events()
        broker.publish_tick(_tick("N", 99, 100))
        events = broker.poll_events()
        assert [event.event_type for event in events] == ["trade", "order", "tick"]
        assert broker.get_positions()[0].long_total == 1
    finally:
        broker.stop()
