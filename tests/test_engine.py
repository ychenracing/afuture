from datetime import datetime, timezone
from pathlib import Path

from afuture.broker.sim import SimBroker
from afuture.engine import TradingEngine
from afuture.models import ContractSpec, PairConfig, Tick
from afuture.risk import RiskConfig, RiskManager
from afuture.state import StateStore


def test_engine_processes_quotes_without_bypassing_strategy_errors(tmp_path: Path):
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1, lookback=3, entry_z=0.8)
    broker = SimBroker(500000, specs)
    engine = TradingEngine(broker, [pair], specs, RiskManager(RiskConfig()), StateStore(tmp_path / "state.json"))
    engine.start()
    for i, spread in enumerate([10, 11, 10, 20]):
        now = datetime(2026, 8, 19, 9, i, tzinfo=timezone.utc)
        broker.publish_tick(Tick("m2609", "DCE", now, 3000 + spread - 1, 3000 + spread + 1, 3000 + spread, 10, 10, "20260819"))
        engine.run_once()
        broker.publish_tick(Tick("m2701", "DCE", now, 2999, 3001, 3000, 10, 10, "20260819"))
        engine.run_once()
    assert broker.get_positions()


def test_engine_persists_kill_switch_on_exception(tmp_path: Path):
    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    store = StateStore(tmp_path / "state.json")
    broker = SimBroker(500000, specs)
    engine = TradingEngine(broker, [], specs, RiskManager(RiskConfig()), store)
    engine.start()
    engine.emergency_stop("test failure")
    assert store.load().kill_switch


def test_engine_start_defers_account_initialization_until_async_broker_ready(tmp_path: Path):
    class AsyncBroker:
        def __init__(self):
            self.started = False
            self.subscriptions = []
        def start(self): self.started = True
        def stop(self): pass
        def is_ready(self): return False
        def subscribe(self, symbol, exchange): self.subscriptions.append((symbol, exchange))
        def get_account(self): raise AssertionError("account must not be queried before broker is ready")
        def get_positions(self): return []
        def get_active_orders(self): return []
        def cancel_order(self, order_id): pass
        def poll_events(self): return []

    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    broker = AsyncBroker()
    engine = TradingEngine(broker, [pair], specs, RiskManager(RiskConfig()), StateStore(tmp_path / "state.json"))
    engine.start()
    assert broker.started
    assert len(broker.subscriptions) == 2


def test_strategy_position_is_restored_only_after_successful_reconciliation(tmp_path: Path):
    from dataclasses import asdict
    from afuture.models import ContractPosition
    from afuture.state import RuntimeState

    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    expected = [
        ContractPosition("m2609", "DCE", long_today=1),
        ContractPosition("m2701", "DCE", short_today=1),
    ]
    broker = SimBroker(500000, specs)
    broker.position_book = __import__("afuture.position", fromlist=["PositionBook"]).PositionBook(expected)
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(positions=[asdict(position) for position in expected]))
    engine = TradingEngine(broker, [pair], specs, RiskManager(RiskConfig()), store)

    engine.start()
    assert engine.reconcile_startup()
    assert engine.strategies["m_pair"].position == 1


def test_initialization_does_not_erase_persisted_position_before_reconciliation(tmp_path: Path):
    from afuture.models import ContractPosition

    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    broker = SimBroker(500000, specs)
    broker.position_book = __import__("afuture.position", fromlist=["PositionBook"]).PositionBook([
        ContractPosition("m2609", "DCE", long_today=1),
        ContractPosition("m2701", "DCE", short_today=1),
    ])
    store = StateStore(tmp_path / "state.json")
    store.save(__import__("afuture.state", fromlist=["RuntimeState"]).RuntimeState(positions=[]))
    engine = TradingEngine(broker, [pair], specs, RiskManager(RiskConfig()), store)
    engine.start()
    assert not engine.reconcile_startup()
    assert engine.halted


def test_engine_does_not_feed_stale_cross_leg_quotes_into_strategy(tmp_path: Path):
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1, lookback=3)
    broker = SimBroker(500000, specs)
    engine = TradingEngine(
        broker, [pair], specs,
        RiskManager(RiskConfig(max_quote_age_seconds=5)),
        StateStore(tmp_path / "state.json"),
    )
    engine.start()
    first = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    broker.publish_tick(Tick("m2609", "DCE", first, 3000, 3001, 3000.5, 10, 10, "20260819"))
    engine.run_once()
    broker.publish_tick(Tick("m2701", "DCE", first, 2990, 2991, 2990.5, 10, 10, "20260819"))
    engine.run_once()
    assert len(engine.strategies["m_pair"].snapshot_state()["history"]) == 1

    later = first.replace(minute=1)
    broker.publish_tick(Tick("m2609", "DCE", later, 3001, 3002, 3001.5, 10, 10, "20260819"))
    engine.run_once()
    # 另一腿仍是一分钟前的报价，策略历史不得被污染。
    assert len(engine.strategies["m_pair"].snapshot_state()["history"]) == 1


def test_reconciliation_mismatch_preserves_expected_positions(tmp_path: Path):
    from afuture.models import ContractPosition
    from afuture.position import PositionBook
    from afuture.state import RuntimeState

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = SimBroker(500000, specs)
    broker.position_book = PositionBook([
        ContractPosition("m2609", "DCE", long_today=2),
    ])
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(positions=[ContractPosition("m2609", "DCE", long_today=1).__dict__.copy()]))
    engine = TradingEngine(broker, [], specs, RiskManager(RiskConfig()), store)

    engine.start()
    assert not engine.reconcile_startup()
    persisted = store.positions_from_state(store.load())
    assert persisted[0].long_today == 1


def test_unknown_trade_event_halts_without_adopting_remote_position(tmp_path: Path):
    from afuture.models import BrokerEvent, ContractPosition, Offset, OrderSide, Trade
    from afuture.state import RuntimeState

    class ExternalTradeBroker(SimBroker):
        def owns_order(self, order_id: str) -> bool:
            return False

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = ExternalTradeBroker(500000, specs)
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(positions=[]))
    engine = TradingEngine(broker, [], specs, RiskManager(RiskConfig()), store)
    engine.start()

    trade = Trade(
        trade_id="external-1",
        order_id="manual-order",
        symbol="m2609",
        exchange="DCE",
        side=OrderSide.BUY,
        offset=Offset.OPEN,
        volume=1,
        price=3000,
        timestamp=datetime.now(timezone.utc),
    )
    broker._events.append(BrokerEvent("trade", trade))
    engine.run_once()

    assert engine.halted
    assert store.positions_from_state(store.load()) == []


def test_position_snapshot_drift_halts_and_preserves_expected_state(tmp_path: Path):
    from afuture.models import BrokerEvent, ContractPosition
    from afuture.state import RuntimeState

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = SimBroker(500000, specs)
    expected = ContractPosition("m2609", "DCE", long_today=1)
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(positions=[expected.__dict__.copy()]))
    engine = TradingEngine(broker, [], specs, RiskManager(RiskConfig()), store)
    engine.start()

    broker._events.append(BrokerEvent(
        "position_snapshot",
        [ContractPosition("m2609", "DCE", long_today=2)],
    ))
    engine.run_once()

    assert engine.halted
    persisted = store.positions_from_state(store.load())
    assert persisted[0].long_today == 1


def test_broker_error_event_halts_trading(tmp_path: Path):
    from afuture.models import BrokerEvent

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = SimBroker(500000, specs)
    engine = TradingEngine(
        broker,
        [],
        specs,
        RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
    )
    engine.start()
    broker._events.append(BrokerEvent("broker_error", "snapshot decode failed"))

    engine.run_once()

    assert engine.halted
    assert "snapshot decode failed" in engine.state.kill_reason


def test_kill_switch_cannot_clear_while_account_risk_is_still_violated(tmp_path: Path):
    from afuture.state import RuntimeState

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = SimBroker(500000, specs)
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(
        kill_switch=True,
        kill_reason="daily loss limit reached",
        reconciled=False,
        trading_day="20260819",
        day_start_equity=500000,
        equity_high_watermark=500000,
        positions=[],
    ))
    broker._balance = 490000
    broker._trading_day = "20260819"
    engine = TradingEngine(
        broker,
        [],
        specs,
        RiskManager(RiskConfig(max_daily_loss_ratio=0.01)),
        store,
    )

    engine.start()

    assert not engine.clear_kill_switch_after_reconcile()
    assert engine.halted
    assert store.load().kill_switch


def test_engine_halts_when_broker_health_watchdog_fails(tmp_path: Path):
    class UnhealthyBroker(SimBroker):
        def health_error(self):
            return "position snapshot stale"

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = UnhealthyBroker(500000, specs)
    engine = TradingEngine(
        broker,
        [],
        specs,
        RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
    )
    engine.start()

    engine.run_once()

    assert engine.halted
    assert "position snapshot stale" in engine.state.kill_reason


def test_startup_rolls_expected_today_positions_when_trading_day_changes(tmp_path: Path):
    from dataclasses import asdict
    from afuture.models import ContractPosition
    from afuture.position import PositionBook
    from afuture.state import RuntimeState

    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1)
    broker = SimBroker(500000, specs)
    broker._trading_day = "20260820"
    broker.position_book = PositionBook([
        ContractPosition("m2609", "DCE", long_yesterday=1),
        ContractPosition("m2701", "DCE", short_yesterday=1),
    ])
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(
        trading_day="20260819",
        day_start_equity=500000,
        positions=[
            asdict(ContractPosition("m2609", "DCE", long_today=1)),
            asdict(ContractPosition("m2701", "DCE", short_today=1)),
        ],
    ))
    engine = TradingEngine(
        broker,
        [pair],
        specs,
        RiskManager(RiskConfig()),
        store,
    )

    engine.start()

    assert engine.reconcile_startup()
    persisted = {position.symbol: position for position in store.positions_from_state(store.load())}
    assert persisted["m2609"].long_yesterday == 1
    assert persisted["m2609"].long_today == 0
    assert persisted["m2701"].short_yesterday == 1
    assert engine.strategies["m_pair"].position == 1


def test_engine_does_not_treat_accepted_zero_fill_fak_as_open_position(tmp_path: Path):
    from afuture.models import BrokerEvent, Order, OrderStatus

    class NoFillSimBroker(SimBroker):
        def send_order(self, request):
            order_id = f"NOFILL-{len(self._orders) + 1}"
            order = Order(order_id, request, status=OrderStatus.CANCELLED)
            self._orders[order_id] = order
            self._events.append(BrokerEvent("order", order))
            return order_id

    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig(
        "m_pair", "m2609", "m2701", "DCE", 1,
        lookback=3, entry_z=0.8, sample_seconds=60,
    )
    broker = NoFillSimBroker(500000, specs)
    engine = TradingEngine(
        broker,
        [pair],
        specs,
        RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
    )
    engine.start()
    base = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    for i, spread in enumerate([10, 11, 10, 20]):
        now = base.replace(minute=i)
        broker.publish_tick(Tick("m2609", "DCE", now, 3000 + spread - 1, 3000 + spread + 1, 3000 + spread, 10, 10, "20260819"))
        engine.run_once()
        broker.publish_tick(Tick("m2701", "DCE", now, 2999, 3001, 3000, 10, 10, "20260819"))
        engine.run_once()

    assert engine.strategies["m_pair"].position != 0
    later = base.replace(minute=3, second=1)
    broker.publish_tick(Tick("m2609", "DCE", later, 3019, 3021, 3020, 10, 10, "20260819"))
    engine.run_once()

    assert engine.strategies["m_pair"].position == 0


def test_engine_does_not_submit_duplicate_pair_orders_while_pair_order_is_active(tmp_path: Path):
    from afuture.models import Offset, Order, OrderRequest, OrderSide, OrderStatus

    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1, lookback=3, entry_z=0.8)
    broker = SimBroker(500000, specs)
    engine = TradingEngine(
        broker,
        [pair],
        specs,
        RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
    )
    engine.start()
    engine.strategies["m_pair"].restore_state({"history": [10, 11, 10], "position": 0})
    broker._orders["existing"] = Order(
        "existing",
        OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 1, 3000, reference="m_pair"),
        status=OrderStatus.NOT_TRADED,
    )
    now = datetime.now(timezone.utc)
    broker.publish_tick(Tick("m2609", "DCE", now, 3019, 3021, 3020, 10, 10, "20260819"))
    engine.run_once()
    broker.publish_tick(Tick("m2701", "DCE", now, 2999, 3001, 3000, 10, 10, "20260819"))
    engine.run_once()

    assert list(broker._orders) == ["existing"]


def test_engine_halts_on_unrecognized_active_order(tmp_path: Path):
    from afuture.models import BrokerEvent, Offset, Order, OrderRequest, OrderSide, OrderStatus

    class ForeignOrderBroker(SimBroker):
        def owns_order(self, order_id: str) -> bool:
            return False

    specs = {"m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1)}
    broker = ForeignOrderBroker(500000, specs)
    store = StateStore(tmp_path / "state.json")
    engine = TradingEngine(broker, [], specs, RiskManager(RiskConfig()), store)
    engine.start()
    foreign = Order(
        "CTP.foreign",
        OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 1, 3000),
        status=OrderStatus.NOT_TRADED,
    )
    broker._events.append(BrokerEvent("order", foreign))

    engine.run_once()

    assert engine.halted
    assert "unrecognized active order" in engine.state.kill_reason
