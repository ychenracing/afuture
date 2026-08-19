from datetime import datetime, timedelta, timezone

from afuture.models import AccountSnapshot, ContractSpec, Offset, OrderRequest, OrderSide, PairConfig, Tick
from afuture.risk import RiskConfig, RiskManager


def account(equity=500000, margin=100000):
    return AccountSnapshot(
        balance=equity,
        equity=equity,
        available=equity-margin,
        margin=margin,
        realized_pnl=0,
        unrealized_pnl=0,
        trading_day="20260819",
    )


def test_risk_checks_combined_pair_margin_not_each_leg_separately():
    manager = RiskManager(RiskConfig(max_margin_ratio=0.30))
    spec = ContractSpec("m2609", "DCE", multiplier=10, price_tick=1, margin_rate_long=0.12, margin_rate_short=0.12)
    other = ContractSpec("m2701", "DCE", multiplier=10, price_tick=1, margin_rate_long=0.12, margin_rate_short=0.12)
    orders = [
        OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 3, 3500),
        OrderRequest("m2701", "DCE", OrderSide.SELL, Offset.OPEN, 3, 3500),
    ]
    decision = manager.check_open_batch(account(equity=500000, margin=130000), orders, {"m2609": spec, "m2701": other}, open_pair_count=0)
    assert not decision.allowed
    assert "margin" in decision.reason


def test_risk_rejects_stale_quotes_and_daily_loss():
    manager = RiskManager(RiskConfig(max_daily_loss_ratio=0.01, max_quote_age_seconds=5))
    manager.set_day_start_equity(500000, "20260819")
    assert not manager.check_account(account(equity=494000, margin=50000)).allowed

    now = datetime.now(timezone.utc)
    stale = Tick("m2609", "DCE", now - timedelta(seconds=6), 3000, 3001, 3000, 1, 1, "20260819")
    assert not manager.check_quotes([stale], now).allowed


def test_expiry_blackout_rejects_new_position():
    manager = RiskManager(RiskConfig(expiry_blackout_days=5))
    pair = PairConfig("rb_pair", "rb2608", "rb2610", "SHFE", 1, expiry_near="2026-08-20", expiry_far="2026-10-20")
    decision = manager.check_pair_calendar(pair, datetime(2026, 8, 19, tzinfo=timezone.utc), opening=True)
    assert not decision.allowed


def test_margin_buffer_and_existing_contract_volume_are_enforced():
    manager = RiskManager(RiskConfig(max_margin_ratio=0.30, max_contract_volume=3, margin_estimate_buffer=1.2))
    spec = ContractSpec("m2609", "DCE", multiplier=10, price_tick=1, margin_rate_long=0.10, margin_rate_short=0.10)
    order = OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 2, 3500)
    # 已持有 2 手时，再开 2 手必须触发单合约总手数上限。
    decision = manager.check_open_batch(
        account(equity=500000, margin=10000),
        [order],
        {"m2609": spec},
        open_pair_count=0,
        current_contract_volumes={"m2609": 2},
    )
    assert not decision.allowed
    assert "contract volume" in decision.reason


def test_high_watermark_can_be_restored_across_restart():
    manager = RiskManager(RiskConfig(max_total_drawdown_ratio=0.08, max_daily_loss_ratio=0.50))
    manager.restore_high_watermark(600000)
    manager.set_day_start_equity(560000, "20260819")
    decision = manager.check_account(account(equity=550000, margin=10000))
    assert not decision.allowed
    assert "drawdown" in decision.reason
