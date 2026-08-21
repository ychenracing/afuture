from datetime import datetime, timedelta, timezone


def _tick(symbol: str, price: float, ts: datetime):
    from afuture.models import Tick

    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=ts,
        bid_price=price,
        ask_price=price,
        last_price=price,
        bid_volume=100,
        ask_volume=100,
        trading_day=ts.strftime("%Y%m%d"),
    )


def test_rejected_exit_preserves_original_entry_anchor_and_advances_history():
    from afuture.models import PairConfig, SignalAction
    from afuture.strategy import CalendarSpreadStrategy

    pair = PairConfig(
        pair_id="m_calendar",
        near_symbol="N",
        far_symbol="F",
        exchange="DCE",
        volume=1,
        lookback=3,
        entry_z=1.0,
        exit_z=0.2,
        stop_z=4.0,
        structural_mean_shift_z=10.0,
        structural_vol_ratio=10.0,
        max_holding_samples=100,
    )
    strategy = CalendarSpreadStrategy(pair)
    start = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    strategy.restore_state(
        {
            "history": [9.0, 10.0, 11.0],
            "position": 1,
            "entry_mean": 10.0,
            "entry_std": 1.25,
            "holding_samples": 5,
            "last_sample_ts": start.isoformat(),
        }
    )
    before = strategy.snapshot_state()

    signal = strategy.on_quotes(
        _tick("N", 110.0, start + timedelta(days=1)),
        _tick("F", 100.0, start + timedelta(days=1)),
    )
    assert signal.action is SignalAction.EXIT
    assert strategy.position == 0

    strategy.restore_after_rejected_signal(before)
    after = strategy.snapshot_state()

    assert after["position"] == 1
    assert after["entry_mean"] == 10.0
    assert after["entry_std"] == 1.25
    assert after["holding_samples"] == 6
    assert after["history"][-1] == 10.0
    assert after["last_sample_ts"] != before["last_sample_ts"]


def test_rejected_open_returns_flat_but_keeps_new_sample():
    from afuture.models import PairConfig, SignalAction
    from afuture.strategy import CalendarSpreadStrategy

    pair = PairConfig(
        pair_id="m_calendar",
        near_symbol="N",
        far_symbol="F",
        exchange="DCE",
        volume=1,
        lookback=3,
        entry_z=1.0,
        exit_z=0.2,
        stop_z=4.0,
    )
    strategy = CalendarSpreadStrategy(pair)
    start = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    strategy.restore_state(
        {
            "history": [0.0, 0.5, -0.5],
            "position": 0,
            "entry_mean": 0.0,
            "entry_std": 0.0,
            "holding_samples": 0,
            "last_sample_ts": start.isoformat(),
        }
    )
    before = strategy.snapshot_state()

    signal = strategy.on_quotes(
        _tick("N", 103.0, start + timedelta(days=1)),
        _tick("F", 100.0, start + timedelta(days=1)),
    )
    assert signal.action is SignalAction.SHORT_SPREAD
    assert strategy.position == -1

    strategy.restore_after_rejected_signal(before)
    after = strategy.snapshot_state()

    assert after["position"] == 0
    assert after["history"][-1] == 3.0
    assert after["last_sample_ts"] != before["last_sample_ts"]
