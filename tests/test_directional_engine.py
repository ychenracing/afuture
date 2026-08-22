from datetime import datetime, timezone

from afuture.directional_engine import DirectionalTradingEngine
from afuture.directional_runtime import DirectionalActionResult
from afuture.models import AccountSnapshot, RuntimeMode, Tick
from afuture.risk import RiskConfig, RiskManager
from afuture.state import StateStore


NOW = datetime(2026, 8, 24, 13, 1, tzinfo=timezone.utc)


class _Manager:
    def __init__(self):
        self.bootstrap_calls = 0
        self.observe_calls = []
        self.rebalance_calls = 0
        self.flatten_calls = 0
        self.risk = False
        self.closed = False

    def bootstrap(self, now):
        self.bootstrap_calls += 1

    def observe(self, tick):
        self.observe_calls.append(tick)

    def maybe_rebalance(self, now):
        self.rebalance_calls += 1
        return DirectionalActionResult("hold")

    def flatten(self, now):
        self.flatten_calls += 1
        return DirectionalActionResult("reduce" if self.risk else "hold")

    def has_risk(self):
        return self.risk

    def required_symbols(self):
        return set()

    def close(self):
        self.closed = True


class _Broker:
    def __init__(self):
        self.ready = False
        self.account = AccountSnapshot(
            balance=100000,
            equity=100000,
            available=100000,
            margin=0,
            realized_pnl=0,
            unrealized_pnl=0,
            trading_day="20260825",
        )

    def start(self):
        self.ready = True

    def stop(self):
        self.ready = False

    def is_ready(self):
        return self.ready

    def subscribe(self, symbol, exchange):
        pass

    def get_account(self):
        return self.account

    def get_positions(self):
        return []

    def get_active_orders(self):
        return []

    def poll_events(self):
        return []

    def health_error(self):
        return None


def _tick():
    return Tick(
        symbol="A2609",
        exchange="DCE",
        timestamp=NOW,
        bid_price=99,
        ask_price=101,
        last_price=100,
        bid_volume=100,
        ask_volume=100,
        volume=10000,
        open_interest=20000,
        trading_day="20260825",
        limit_up=120,
        limit_down=80,
    )


def test_directional_engine_forwards_ticks_and_runs_manager(tmp_path):
    broker = _Broker()
    manager = _Manager()
    engine = DirectionalTradingEngine(
        broker,
        [],
        {},
        RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
        directional_manager=manager,
        health_clock=lambda: NOW,
    )
    engine.start()
    assert manager.bootstrap_calls == 1

    tick = _tick()
    engine.on_tick(tick)
    assert manager.observe_calls == [tick]

    engine.run_once()
    assert manager.rebalance_calls == 1
    engine.stop()
    assert manager.closed is True


def test_directional_reduce_only_flattens_before_halting(tmp_path):
    broker = _Broker()
    manager = _Manager()
    engine = DirectionalTradingEngine(
        broker,
        [],
        {},
        RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
        directional_manager=manager,
        health_clock=lambda: NOW,
    )
    engine.start()
    manager.risk = True
    engine.enter_reduce_only("directional test")
    assert engine.state.runtime_mode == RuntimeMode.REDUCE_ONLY.value

    engine.run_once()
    assert manager.flatten_calls >= 1
    assert engine.halted is False

    manager.risk = False
    engine.run_once()
    assert engine.halted is True
    assert engine.state.runtime_mode == RuntimeMode.HALTED.value
    engine.stop()
