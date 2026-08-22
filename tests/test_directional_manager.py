from datetime import datetime, timezone

import pandas as pd

from afuture.directional import DirectionalConfig
from afuture.directional_runtime import DirectionalPortfolioManager
from afuture.models import (
    AccountSnapshot,
    ContractInfo,
    ContractPosition,
    ContractSpec,
    Offset,
    OrderType,
    Tick,
)
from afuture.risk import RiskConfig, RiskManager


NOW = datetime(2026, 8, 24, 13, 1, tzinfo=timezone.utc)  # 21:01 China


def _tick(symbol: str, oi: float, *, depth: float = 1000.0) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=NOW,
        bid_price=99.0,
        ask_price=101.0,
        last_price=100.0,
        bid_volume=depth,
        ask_volume=depth,
        volume=20000,
        open_interest=oi,
        trading_day="20260825",
        limit_up=120.0,
        limit_down=80.0,
    )


class _Provider:
    def __init__(self):
        dates = pd.date_range("2025-12-01", periods=180, freq="B")
        self.frame = pd.DataFrame({"A": range(100, 280)}, index=dates, dtype=float)
        self.calls = 0

    def load(self, products):
        self.calls += 1
        return self.frame[list(products)].copy()


class _Policy:
    def target_weights(self, close):
        assert not close.empty
        return {"A": 1.0}


class _Broker:
    metadata_query_blocks = False

    def __init__(self, *, depth=1000.0):
        self.catalog = [
            ContractInfo("A2609", "DCE", "A", "2026-09-15"),
            ContractInfo("A2611", "DCE", "A", "2026-11-15"),
        ]
        self.specs = {
            symbol: ContractSpec(symbol, "DCE", 10, 1, 0.1, 0.1)
            for symbol in ("A2609", "A2611")
        }
        self.positions = [ContractPosition("A2609", "DCE", long_today=2)]
        self.orders = []
        self.subscriptions = []
        self.ticks = {
            "A2609": _tick("A2609", 10000, depth=depth),
            "A2611": _tick("A2611", 20000, depth=depth),
        }
        self.account = AccountSnapshot(
            balance=100000,
            equity=100000,
            available=100000,
            margin=0,
            realized_pnl=0,
            unrealized_pnl=0,
            trading_day="20260825",
        )

    def is_ready(self):
        return True

    def get_contract_catalog(self):
        return list(self.catalog)

    def subscribe(self, symbol, exchange):
        self.subscriptions.append((symbol, exchange))

    def get_live_contract_specs(self, symbols, timeout_seconds=10.0):
        return {symbol: self.specs[symbol] for symbol in symbols}

    def get_account(self):
        return self.account

    def get_positions(self):
        return list(self.positions)

    def get_active_orders(self):
        return []

    def send_order(self, request):
        self.orders.append(request)
        return f"order-{len(self.orders)}"

    def cancel_order(self, order_id):
        pass


def _manager(broker):
    config = DirectionalConfig(
        enabled=True,
        products=("A",),
        exchanges=("DCE",),
        max_gross_leverage=2.0,
        max_contract_volume=5,
        rebalance_window="21:00-21:10",
    )
    risk = RiskManager(
        RiskConfig(
            max_margin_ratio=0.50,
            min_available_ratio=0.20,
            max_contract_volume=5,
            min_depth_multiple=2.0,
            open_cooldown_minutes=0,
            close_blackout_minutes=0,
        )
    )
    return DirectionalPortfolioManager(
        config,
        broker,
        risk,
        signal_provider=_Provider(),
        policy=_Policy(),
        aggressive_ticks=1,
    )


def test_manager_subscribes_universe_and_reduces_before_opening_new_main_contract():
    broker = _Broker()
    manager = _manager(broker)
    manager.bootstrap(NOW)
    assert set(broker.subscriptions) == {("A2609", "DCE"), ("A2611", "DCE")}
    for tick in broker.ticks.values():
        manager.observe(tick)

    result = manager.maybe_rebalance(NOW)
    assert result.action == "reduce"
    assert broker.orders
    assert all(order.offset is not Offset.OPEN for order in broker.orders)
    assert all(order.order_type is OrderType.FAK for order in broker.orders)
    assert broker.orders[0].symbol == "A2609"

    # After the broker confirms the reduction, the next cycle may add the new main risk.
    broker.positions = []
    broker.orders.clear()
    result = manager.maybe_rebalance(NOW)
    assert result.action == "open"
    assert broker.orders
    assert all(order.offset is Offset.OPEN for order in broker.orders)
    assert broker.orders[0].symbol == "A2611"
    assert broker.orders[0].volume == 5


def test_manager_fails_closed_when_opening_quote_depth_is_insufficient():
    broker = _Broker(depth=1.0)
    broker.positions = []
    manager = _manager(broker)
    manager.bootstrap(NOW)
    for tick in broker.ticks.values():
        manager.observe(tick)

    result = manager.maybe_rebalance(NOW)
    assert result.action == "reject"
    assert "depth" in result.reason
    assert broker.orders == []


def test_manager_flatten_only_emits_reducing_fak_orders():
    broker = _Broker()
    manager = _manager(broker)
    manager.bootstrap(NOW)
    for tick in broker.ticks.values():
        manager.observe(tick)

    result = manager.flatten(NOW)
    assert result.action == "reduce"
    assert broker.orders
    assert all(order.offset is not Offset.OPEN for order in broker.orders)
    assert all(order.order_type is OrderType.FAK for order in broker.orders)
