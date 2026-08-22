from datetime import datetime, timezone

import afuture.execution_aligned_policy as policy
from afuture.directional_engine import DirectionalTradingEngine
from afuture.directional_runtime import DirectionalActionResult
from afuture.models import AccountSnapshot, RuntimeMode, Tick
from afuture.risk import RiskConfig, RiskManager
from afuture.state import StateStore


NOW = datetime(2026, 8, 24, 13, 1, tzinfo=timezone.utc)


class _Manager:
    def __init__(self):
        self.risk = True

    def bootstrap(self, now):
        return None

    def observe(self, tick):
        return None

    def maybe_rebalance(self, now):
        return DirectionalActionResult("hold")

    def flatten(self, now):
        return DirectionalActionResult("reduce" if self.risk else "hold")

    def has_risk(self):
        return self.risk

    def required_symbols(self):
        return set()

    def close(self):
        return None

    def directional_order_expectation(self, order_id):
        return None

    def note_directional_quality_order(self, order):
        return None

    def note_directional_quality_fill(self, trade, **kwargs):
        return None

    def _finalize_quality_cycle_if_settled(self, now):
        return None


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
        return None

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

    def owns_order(self, order_id):
        return True


def _tick(trading_day="20260825"):
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
        trading_day=trading_day,
        limit_up=120,
        limit_down=80,
    )


def test_production_meta_shape_is_frozen_to_100pct_candidate():
    assert policy.META_LOOKBACK == 11
    assert policy.META_REBALANCE == 3
    assert policy.META_COUNT == 3
    assert policy.META_ANNUALIZED_WEIGHT == 0.25
    assert policy.META_SHARPE_WEIGHT == 1.0


def test_daily_loss_is_a_same_trading_day_lock_not_a_permanent_kill(tmp_path):
    broker = _Broker()
    manager = _Manager()
    risk = RiskManager(
        RiskConfig(max_daily_loss_ratio=0.05, max_total_drawdown_ratio=0.30)
    )
    engine = DirectionalTradingEngine(
        broker,
        [],
        {},
        risk,
        StateStore(tmp_path / "state.json"),
        directional_manager=manager,
        health_clock=lambda: NOW,
    )
    engine.start()

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
    assert engine.state.daily_loss_lock_trading_day == "20260825"

    manager.risk = False
    engine.run_once()
    assert engine.state.runtime_mode == RuntimeMode.REDUCE_ONLY.value
    assert engine.halted is False

    broker.account = AccountSnapshot(
        balance=90000,
        equity=90000,
        available=90000,
        margin=0,
        realized_pnl=-10000,
        unrealized_pnl=0,
        trading_day="20260826",
    )
    engine._handle_account_event(broker.account)
    engine.run_once()
    assert engine.state.runtime_mode == RuntimeMode.RUNNING.value
    assert engine.state.daily_loss_lock_trading_day == ""
    assert engine.halted is False
