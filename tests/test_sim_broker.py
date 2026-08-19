from datetime import datetime, timezone

from afuture.broker.sim import SimBroker
from afuture.models import ContractSpec, FeeSpec, Offset, OrderRequest, OrderSide, OrderStatus, OrderType, Tick


def test_sim_broker_fills_marketable_limit_and_charges_fee():
    spec = ContractSpec(
        "m2609", "DCE", multiplier=10, price_tick=1,
        margin_rate_long=0.1, margin_rate_short=0.1,
        fee=FeeSpec(open_fixed=2.0, close_fixed=2.0),
    )
    broker = SimBroker(500000, {"m2609": spec})
    broker.start()
    broker.publish_tick(Tick("m2609", "DCE", datetime.now(timezone.utc), 2999, 3000, 2999.5, 10, 10, "20260819"))
    order_id = broker.send_order(OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 2, 3001))
    order = broker.get_order(order_id)
    assert order.status is OrderStatus.FILLED
    assert order.traded == 2
    assert broker.get_positions()[0].long_today == 2
    assert broker.get_account().balance == 500000 - 4


def test_sim_broker_keeps_non_marketable_order_then_fills_on_new_tick():
    spec = ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)
    broker = SimBroker(500000, {"m2609": spec})
    broker.start()
    broker.publish_tick(Tick("m2609", "DCE", datetime.now(timezone.utc), 3000, 3001, 3000.5, 10, 10, "20260819"))
    order_id = broker.send_order(OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 1, 2999))
    assert broker.get_order(order_id).status is OrderStatus.NOT_TRADED
    broker.publish_tick(Tick("m2609", "DCE", datetime.now(timezone.utc), 2998, 2999, 2998.5, 10, 10, "20260819"))
    assert broker.get_order(order_id).status is OrderStatus.FILLED


def test_sim_broker_keeps_trade_history():
    spec = ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)
    broker = SimBroker(100000, {"m2609": spec})
    broker.start()
    now = datetime.now(timezone.utc)
    broker.publish_tick(Tick("m2609", "DCE", now, 3000, 3001, 3000.5, 10, 10, "20260819"))
    broker.send_order(OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 1, 3002, OrderType.FAK))
    assert len(broker.get_trades()) == 1
