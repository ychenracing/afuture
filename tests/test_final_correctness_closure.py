from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from afuture.auto import AutoConfig, AutoPairSelector
from afuture.broker.sim import SimBroker
from afuture.engine import TradingEngine
from afuture.execution import PairExecutor
from afuture.models import (
    ContractInfo,
    ContractSpec,
    Offset,
    OrderRequest,
    OrderSide,
    OrderType,
    PairConfig,
    RuntimeMode,
    SignalAction,
    SpreadSignal,
    Tick,
)
from afuture.portfolio_risk import PortfolioRiskAnalyzer
from afuture.risk import RiskConfig, RiskManager
from afuture.state import StateStore
from afuture.strategy import CalendarSpreadStrategy


def _tick(symbol: str, ts: datetime, bid: float = 99, ask: float = 100) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=ts,
        bid_price=bid,
        ask_price=ask,
        last_price=(bid + ask) / 2,
        bid_volume=100,
        ask_volume=100,
        trading_day=ts.astimezone(timezone.utc).strftime("%Y%m%d"),
        volume=20000,
        open_interest=80000,
    )


def _specs():
    return {
        "N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15),
        "F": ContractSpec("F", "DCE", 10, 1, 0.15, 0.15),
    }


def test_market_session_uses_asia_shanghai_for_utc_ticks():
    timestamp = datetime(2026, 8, 21, 1, 30, tzinfo=timezone.utc)  # 09:30 China
    pair = PairConfig("p", "N", "F", "DCE", 1, session_windows=("09:00-10:00",))
    decision = RiskManager(
        RiskConfig(min_depth_multiple=1.0, open_cooldown_minutes=0, close_blackout_minutes=0)
    ).check_market_entry(
        pair,
        _tick("N", timestamp),
        _tick("F", timestamp),
        SignalAction.LONG_SPREAD,
        1,
        _specs(),
    )
    assert decision.allowed, decision.reason


def test_expiry_blackout_uses_asia_shanghai_calendar_date():
    timestamp = datetime(2026, 8, 21, 16, 30, tzinfo=timezone.utc)  # Aug 22 China
    pair = PairConfig(
        "p", "N", "F", "DCE", 1,
        expiry_near="2026-08-22", expiry_far="2026-09-22",
    )
    decision = RiskManager(RiskConfig(expiry_blackout_days=0)).check_pair_calendar(
        pair, timestamp, opening=True
    )
    assert not decision.allowed
    assert "expiry blackout" in decision.reason


def test_pair_executor_rate_limit_uses_signal_event_time_by_default():
    specs = _specs()
    broker = SimBroker(500000, specs)
    broker.start()
    try:
        risk = RiskManager(
            RiskConfig(max_orders_per_minute=2, min_depth_multiple=1.0, max_quote_age_seconds=30.0)
        )
        executor = PairExecutor(broker, risk, specs, aggressive_ticks=0, slippage_ticks=0)
        pair = PairConfig("p", "N", "F", "DCE", 1)
        day1 = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        near1, far1 = _tick("N", day1), _tick("F", day1, 89, 90)
        broker.publish_tick(near1); broker.publish_tick(far1); broker.poll_events()
        opened = executor.execute_signal(
            pair,
            SpreadSignal("p", SignalAction.LONG_SPREAD, -2.0, day1, 11, 20, 1),
            near1, far1, open_pair_count=0, spread_std=1,
        )
        assert opened.accepted, opened.reason

        day2 = day1 + timedelta(days=1)
        near2, far2 = _tick("N", day2), _tick("F", day2, 89, 90)
        broker.publish_tick(near2); broker.publish_tick(far2); broker.poll_events()
        closed = executor.execute_signal(
            pair,
            SpreadSignal("p", SignalAction.EXIT, 0.0, day2, 11, 20, 1),
            near2, far2, open_pair_count=1, spread_std=1,
        )
        assert closed.accepted, closed.reason
        assert broker.get_positions() == []
    finally:
        broker.stop()


def test_historical_global_health_does_not_halt_sparse_replay():
    specs = _specs()
    broker = SimBroker(500000, specs)
    broker.start()
    with TemporaryDirectory(prefix="afuture-health-clock-") as temp:
        engine = TradingEngine(
            broker,
            [PairConfig("p", "N", "F", "DCE", 1)],
            specs,
            RiskManager(RiskConfig(max_quote_age_seconds=10.0)),
            StateStore(Path(temp) / "state.json"),
            historical_mode=True,
        )
        start = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
        engine.quotes = {
            "N": _tick("N", start),
            "F": _tick("F", start + timedelta(minutes=1), 89, 90),
        }
        assert engine._market_health_reason() == ""
    broker.stop()


def test_historical_legging_timeout_uses_market_event_time():
    specs = _specs()
    pair = PairConfig("p", "N", "F", "DCE", 1)
    broker = SimBroker(500000, specs)
    with TemporaryDirectory(prefix="afuture-leg-clock-") as temp:
        engine = TradingEngine(
            broker,
            [pair],
            specs,
            RiskManager(RiskConfig(max_orders_per_minute=100)),
            StateStore(Path(temp) / "state.json"),
            historical_mode=True,
            legging_timeout_seconds=2.0,
        )
        engine.start()
        try:
            start = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
            near, far = _tick("N", start), _tick("F", start, 89, 90)
            broker.publish_tick(near); broker.publish_tick(far); broker.poll_events()
            broker.send_order(
                OrderRequest("N", "DCE", OrderSide.BUY, Offset.OPEN, 1, 100, OrderType.FAK, "p")
            )
            broker.poll_events()
            engine.quotes = {"N": near, "F": far}
            engine._audit_pair_balance()
            assert engine.state.runtime_mode == RuntimeMode.RUNNING.value

            later = start + timedelta(seconds=3)
            engine.quotes = {"N": _tick("N", later), "F": _tick("F", later, 89, 90)}
            engine._audit_pair_balance()
            assert engine.state.runtime_mode == RuntimeMode.REDUCE_ONLY.value
        finally:
            engine.stop()


def test_sim_delayed_fill_events_precede_same_tick_strategy_event():
    broker = SimBroker(
        500000,
        {"N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15)},
        conservative=True,
        latency_ticks=1,
    )
    broker.start()
    try:
        ts = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
        broker.publish_tick(_tick("N", ts)); broker.poll_events()
        broker.send_order(OrderRequest("N", "DCE", OrderSide.BUY, Offset.OPEN, 1, 100, OrderType.FAK, "p"))
        broker.poll_events()
        broker.publish_tick(_tick("N", ts + timedelta(seconds=1)))
        assert [event.event_type for event in broker.poll_events()] == ["trade", "order", "tick"]
    finally:
        broker.stop()


def test_portfolio_correlation_uses_common_time_buckets_only():
    analyzer = PortfolioRiskAnalyzer(window=10, min_samples=4, bucket_seconds=60)
    base = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
    for index, value in enumerate([1, 2, 4, 3, 6, 5]):
        analyzer.update("left", value, base + timedelta(minutes=index))
        analyzer.update("right", value * 2, base + timedelta(minutes=index + 10))
    assert analyzer.correlation("left", "right") == 0.0

    aligned = PortfolioRiskAnalyzer(window=10, min_samples=4, bucket_seconds=60)
    for index, value in enumerate([1, 2, 4, 3, 6, 5]):
        timestamp = base + timedelta(minutes=index)
        aligned.update("left", value, timestamp)
        aligned.update("right", value * 2, timestamp + timedelta(seconds=15))
    assert aligned.correlation("left", "right") > 0.99


def test_auto_catalog_honors_historical_listing_date():
    config = AutoConfig(
        enabled=True,
        products=("m",),
        exchanges=("DCE",),
        max_contracts_per_product=3,
        min_days_to_expiry=0,
    )
    catalog = [
        ContractInfo("m2609", "DCE", "m", "2026-09-15", listing="2025-09-15"),
        ContractInfo("m2701", "DCE", "m", "2027-01-15", listing="2026-01-15"),
        ContractInfo("m2705", "DCE", "m", "2027-05-15", listing="2026-09-01"),
    ]
    pairs = AutoPairSelector(config).build_pairs(catalog, date(2026, 8, 21))
    assert [(pair.near_symbol, pair.far_symbol) for pair in pairs] == [("m2609", "m2701")]


def test_auto_refresh_retains_only_open_pairs_and_fails_closed():
    class RecordingManager:
        initialized = True
        last_eligible_ids = set()
        def __init__(self, fail=False):
            self.fail = fail
            self.retained = None
        def refresh_if_needed(self, broker, today, *, retained_pairs=()):
            self.retained = list(retained_pairs)
            if self.fail:
                raise RuntimeError("catalog unavailable")
            return True
        def select(self, broker, *, now, protected_pair_ids):
            return None

    specs = {
        "N1": ContractSpec("N1", "DCE", 10, 1, 0.15, 0.15),
        "F1": ContractSpec("F1", "DCE", 10, 1, 0.15, 0.15),
        "N2": ContractSpec("N2", "DCE", 10, 1, 0.15, 0.15),
        "F2": ContractSpec("F2", "DCE", 10, 1, 0.15, 0.15),
    }
    old_pair = PairConfig("old", "N1", "F1", "DCE", 1, risk_group="m")
    open_pair = PairConfig("open", "N2", "F2", "DCE", 1, risk_group="m")

    for fail in (False, True):
        manager = RecordingManager(fail)
        broker = SimBroker(500000, specs)
        with TemporaryDirectory(prefix="afuture-auto-refresh-") as temp:
            engine = TradingEngine(
                broker, [old_pair, open_pair], specs,
                RiskManager(RiskConfig()), StateStore(Path(temp) / "state.json"),
                auto_manager=manager, historical_mode=True,
            )
            engine._auto_pair_ids = {"old", "open"}
            engine._open_auto_pair_ids = lambda: {"open"}
            engine._trading_date = lambda: date(2026, 8, 21)
            engine._refresh_auto_pairs(datetime(2026, 8, 21, tzinfo=timezone.utc))
            assert manager.retained == [open_pair]
            if fail:
                assert engine._retiring_auto_pairs == {"old"}


def test_rejected_signal_restores_real_position_anchors_but_keeps_observation():
    pair = PairConfig(
        "p", "N", "F", "DCE", 1,
        lookback=3, entry_z=1.0, exit_z=0.2, stop_z=4.0,
        structural_mean_shift_z=10.0, structural_vol_ratio=10.0,
    )
    strategy = CalendarSpreadStrategy(pair)
    start = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    strategy.restore_state({
        "history": [9.0, 10.0, 11.0],
        "raw_history": [9.0, 10.0, 11.0],
        "position": 1,
        "entry_mean": 10.0,
        "entry_std": 1.25,
        "holding_samples": 5,
        "last_sample_ts": start.isoformat(),
    })
    before = strategy.snapshot_state()
    signal = strategy.on_quotes(
        _tick("N", start + timedelta(days=1), 110, 110),
        _tick("F", start + timedelta(days=1), 100, 100),
    )
    assert signal.action is SignalAction.EXIT
    strategy.restore_after_rejected_signal(before)
    after = strategy.snapshot_state()
    assert after["position"] == 1
    assert after["entry_mean"] == 10.0
    assert after["entry_std"] == 1.25
    assert after["holding_samples"] == 6
    assert after["history"][-1] == 10.0
    assert after["last_sample_ts"] != before["last_sample_ts"]
