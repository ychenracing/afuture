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
        self.next_result = DirectionalActionResult("hold")

    def bootstrap(self, now):
        self.bootstrap_calls += 1

    def observe(self, tick):
        self.observe_calls.append(tick)

    def maybe_rebalance(self, now):
        self.rebalance_calls += 1
        return self.next_result

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


def _engine(tmp_path, manager=None, risk=None):
    broker = _Broker()
    manager = manager or _Manager()
    engine = DirectionalTradingEngine(
        broker,
        [],
        {},
        risk or RiskManager(RiskConfig()),
        StateStore(tmp_path / "state.json"),
        directional_manager=manager,
        health_clock=lambda: NOW,
    )
    engine.start()
    return broker, manager, engine


def test_directional_engine_forwards_ticks_and_runs_manager(tmp_path):
    broker, manager, engine = _engine(tmp_path)
    assert manager.bootstrap_calls == 1

    tick = _tick()
    engine.on_tick(tick)
    assert manager.observe_calls == [tick]

    engine.run_once()
    assert manager.rebalance_calls == 1
    engine.stop()
    assert manager.closed is True


def test_directional_signal_risk_off_enters_reduce_only_only_when_risk_exists(tmp_path):
    _, manager, engine = _engine(tmp_path)
    manager.risk = True
    manager.next_result = DirectionalActionResult(
        "risk_off", "required signal trading day unavailable"
    )
    engine.run_once()
    assert engine.state.runtime_mode == RuntimeMode.REDUCE_ONLY.value
    assert engine.halted is False

    _, flat_manager, flat_engine = _engine(tmp_path / "flat")
    flat_manager.risk = False
    flat_manager.next_result = DirectionalActionResult(
        "risk_off", "required signal trading day unavailable"
    )
    flat_engine.run_once()
    assert flat_engine.state.runtime_mode == RuntimeMode.RUNNING.value
    assert flat_engine.halted is False


def test_directional_account_risk_breach_reduces_existing_risk_instead_of_halting(tmp_path):
    risk = RiskManager(
        RiskConfig(max_daily_loss_ratio=0.05, max_total_drawdown_ratio=0.30)
    )
    broker, manager, engine = _engine(tmp_path, risk=risk)
    manager.risk = True
    broker.account = AccountSnapshot(
        balance=90000,
        equity=90000,
        available=90000,
        margin=0,
        realized_pnl=-10000,
        unrealized_pnl=0,
        trading_day="20260825",
    )
    engine.on_tick(_tick())
    assert engine.state.runtime_mode == RuntimeMode.REDUCE_ONLY.value
    assert engine.halted is False


def test_directional_reduce_only_flattens_before_halting(tmp_path):
    broker, manager, engine = _engine(tmp_path)
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
