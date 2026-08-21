from datetime import datetime, timezone

from afuture.models import ContractSpec, PairConfig, SignalAction, Tick
from afuture.risk import RiskConfig, RiskManager


def _tick(symbol: str, timestamp: datetime) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=timestamp,
        bid_price=99,
        ask_price=100,
        last_price=99.5,
        bid_volume=20,
        ask_volume=20,
        trading_day="20260821",
    )


def test_market_session_uses_asia_shanghai_for_utc_ticks():
    # 01:30 UTC is 09:30 in Shanghai and must be inside the configured day session.
    timestamp = datetime(2026, 8, 21, 1, 30, tzinfo=timezone.utc)
    pair = PairConfig(
        "p",
        "N",
        "F",
        "DCE",
        1,
        session_windows=("09:00-10:00",),
    )
    specs = {
        "N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15),
        "F": ContractSpec("F", "DCE", 10, 1, 0.15, 0.15),
    }
    decision = RiskManager(
        RiskConfig(min_depth_multiple=1.0, open_cooldown_minutes=0, close_blackout_minutes=0)
    ).check_market_entry(
        pair,
        _tick("N", timestamp),
        _tick("F", timestamp),
        SignalAction.LONG_SPREAD,
        1,
        specs,
    )
    assert decision.allowed, decision.reason
