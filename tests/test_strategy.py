from datetime import datetime, timedelta, timezone

from afuture.models import PairConfig, SignalAction, Tick
from afuture.strategy import CalendarSpreadStrategy


def tick(symbol: str, bid: float, ask: float, minute: int) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=datetime(2026, 8, 19, 9, minute, tzinfo=timezone.utc),
        bid_price=bid,
        ask_price=ask,
        last_price=(bid + ask) / 2,
        bid_volume=20,
        ask_volume=20,
        trading_day="20260819",
    )


def test_strategy_waits_for_warmup_then_opens_and_exits():
    pair = PairConfig(
        pair_id="m_calendar",
        near_symbol="m2609",
        far_symbol="m2701",
        exchange="DCE",
        volume=1,
        lookback=5,
        entry_z=1.0,
        exit_z=0.2,
        stop_z=4.0,
    )
    strategy = CalendarSpreadStrategy(pair)

    signals = []
    spreads = [10, 11, 9, 10, 10, 15, 10]
    for i, spread in enumerate(spreads):
        near = tick("m2609", 3000 + spread - 1, 3000 + spread + 1, i)
        far = tick("m2701", 2999, 3001, i)
        signals.append(strategy.on_quotes(near, far))

    assert all(s.action is SignalAction.HOLD for s in signals[:4])
    assert signals[5].action is SignalAction.SHORT_SPREAD
    assert signals[6].action is SignalAction.EXIT


def test_strategy_triggers_emergency_exit_when_zscore_exceeds_stop():
    pair = PairConfig(
        pair_id="m_calendar",
        near_symbol="m2609",
        far_symbol="m2701",
        exchange="DCE",
        volume=1,
        lookback=5,
        entry_z=0.8,
        exit_z=0.2,
        stop_z=1.5,
    )
    strategy = CalendarSpreadStrategy(pair)
    base = [10, 10, 10, 10, 11]
    for i, spread in enumerate(base):
        strategy.on_quotes(
            tick("m2609", 3000 + spread - 1, 3000 + spread + 1, i),
            tick("m2701", 2999, 3001, i),
        )
    # 人工标记已有价差仓位，然后给出极端偏离。
    strategy.set_position(1)
    signal = strategy.on_quotes(
        tick("m2609", 3039, 3041, 10),
        tick("m2701", 2999, 3001, 10),
    )
    assert signal.action is SignalAction.EMERGENCY_EXIT


def test_strategy_state_can_be_restored_without_losing_warmup_history():
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1, lookback=3, entry_z=1.0, exit_z=0.2)
    strategy = CalendarSpreadStrategy(pair)
    base = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    for i, spread in enumerate([10.0, 11.0, 12.0]):
        near = Tick("m2609", "DCE", base + timedelta(minutes=i), 3000 + spread - 0.5, 3000 + spread + 0.5, 3000 + spread, 10, 10, "20260819")
        far = Tick("m2701", "DCE", base + timedelta(minutes=i), 2999.5, 3000.5, 3000, 10, 10, "20260819")
        strategy.on_quotes(near, far)
    restored = CalendarSpreadStrategy(pair)
    restored.restore_state(strategy.snapshot_state())
    assert len(restored.snapshot_state()["history"]) == 3
    assert restored.position == strategy.position
