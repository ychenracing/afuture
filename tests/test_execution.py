from datetime import datetime, timezone

from afuture.execution import PairExecutor
from afuture.models import (
    AccountSnapshot, ContractSpec, Offset, OrderRequest, OrderSide, PairConfig, SignalAction,
    SpreadSignal, Tick,
)
from afuture.risk import RiskConfig, RiskManager


class FakeBroker:
    def __init__(self, reject_second=False):
        self.requests = []
        self.reject_second = reject_second
        self.cancelled = []

    def is_ready(self): return True
    def get_account(self): return AccountSnapshot(500000, 500000, 500000, 0, 0, 0, "20260819")
    def get_positions(self): return []
    def get_active_orders(self): return []
    def send_order(self, request):
        self.requests.append(request)
        if self.reject_second and len(self.requests) == 2:
            raise RuntimeError("second leg rejected")
        return f"o{len(self.requests)}"
    def cancel_order(self, order_id): self.cancelled.append(order_id)


def test_pair_executor_sends_both_open_legs_with_aggressive_limits():
    broker = FakeBroker()
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    executor = PairExecutor(broker, RiskManager(RiskConfig()), specs)
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    near = Tick("m2609", "DCE", datetime.now(timezone.utc), 3000, 3001, 3000.5, 10, 10, "20260819")
    far = Tick("m2701", "DCE", datetime.now(timezone.utc), 2980, 2981, 2980.5, 10, 10, "20260819")
    signal = SpreadSignal("m_pair", SignalAction.LONG_SPREAD, -2.1, datetime.now(timezone.utc), 20, -2.1)
    result = executor.execute_signal(pair, signal, near, far, open_pair_count=0)
    assert result.accepted
    assert [(r.side, r.offset) for r in broker.requests] == [
        (OrderSide.BUY, Offset.OPEN),
        (OrderSide.SELL, Offset.OPEN),
    ]


def test_pair_executor_cancels_first_leg_order_if_second_submission_fails():
    broker = FakeBroker(reject_second=True)
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    executor = PairExecutor(broker, RiskManager(RiskConfig()), specs)
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    now = datetime.now(timezone.utc)
    near = Tick("m2609", "DCE", now, 3000, 3001, 3000.5, 10, 10, "20260819")
    far = Tick("m2701", "DCE", now, 2980, 2981, 2980.5, 10, 10, "20260819")
    signal = SpreadSignal("m_pair", SignalAction.LONG_SPREAD, -2.1, now, 20, -2.1)
    result = executor.execute_signal(pair, signal, near, far, open_pair_count=0)
    assert not result.accepted
    assert broker.cancelled == ["o1"]


def test_aggressive_order_price_is_capped_by_daily_price_limit():
    broker = FakeBroker()
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    executor = PairExecutor(broker, RiskManager(RiskConfig()), specs, aggressive_ticks=5)
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    now = datetime.now(timezone.utc)
    near = Tick("m2609", "DCE", now, 3000, 3001, 3000.5, 10, 10, "20260819", limit_up=3003, limit_down=2800)
    far = Tick("m2701", "DCE", now, 2980, 2981, 2980.5, 10, 10, "20260819", limit_up=3100, limit_down=2978)
    signal = SpreadSignal("m_pair", SignalAction.LONG_SPREAD, -2.1, now, 20, 0)
    result = executor.execute_signal(pair, signal, near, far, open_pair_count=0)
    assert result.accepted
    assert broker.requests[0].price == 3003
    assert broker.requests[1].price == 2978


def test_pair_executor_flattens_partial_first_leg_when_second_submission_fails():
    from afuture.broker.sim import SimBroker

    class RejectSecondSimBroker(SimBroker):
        def __init__(self, capital, specs):
            super().__init__(capital, specs)
            self.send_calls = 0

        def send_order(self, request):
            self.send_calls += 1
            if self.send_calls == 2:
                raise RuntimeError("second leg rejected")
            return super().send_order(request)

    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    broker = RejectSecondSimBroker(500000, specs)
    broker.start()
    now = datetime.now(timezone.utc)
    near = Tick("m2609", "DCE", now, 3000, 3001, 3000.5, 10, 1, "20260819")
    far = Tick("m2701", "DCE", now, 2980, 2981, 2980.5, 10, 10, "20260819")
    broker.publish_tick(near)
    broker.publish_tick(far)
    executor = PairExecutor(broker, RiskManager(RiskConfig()), specs)
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 2)
    signal = SpreadSignal("m_pair", SignalAction.LONG_SPREAD, -2.1, now, 20, 0)

    result = executor.execute_signal(pair, signal, near, far, open_pair_count=0)

    assert not result.accepted
    assert broker.get_positions() == []
    assert broker.send_calls == 3
