from datetime import date, datetime, timezone
from pathlib import Path

from afuture.auto import AutoConfig, AutoPairManager
from afuture.broker.sim import SimBroker
from afuture.engine import TradingEngine
from afuture.models import ContractInfo, ContractSpec, Tick
from afuture.risk import RiskConfig, RiskManager
from afuture.state import StateStore


def tick(symbol: str, mid: float, trading_day: str = "20260821") -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=datetime(2026, 8, 21, 6, 56, tzinfo=timezone.utc),
        bid_price=mid - 0.5,
        ask_price=mid + 0.5,
        last_price=mid,
        bid_volume=100,
        ask_volume=100,
        trading_day=trading_day,
        volume=20000,
        open_interest=80000,
    )


def auto_config() -> AutoConfig:
    return AutoConfig(
        enabled=True,
        products=("m",),
        exchanges=("DCE",),
        max_active_pairs=1,
        max_contracts_per_product=2,
        min_days_to_expiry=15,
        scan_interval_seconds=0,
        lookback=4,
        entry_z=2.5,
        exit_z=0.75,
        stop_z=4.0,
        signal_transform="log_ratio",
        confirm_entry=True,
        confirmation_retrace_z=0.3,
        min_confirmed_entry_z=1.75,
        entry_trend_window=3,
        max_entry_z_slope=0.75,
        min_stationarity_score=0.0,
        max_half_life=999.0,
        daily_sample_window="14:55-15:00",
        min_volume=0,
        min_open_interest=0,
        min_liquidity_score=0,
        min_net_edge=0,
    )


def test_engine_persists_and_restores_auto_warmup_history(tmp_path: Path):
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    catalog = [
        ContractInfo("m2609", "DCE", "m", "2026-09-15"),
        ContractInfo("m2701", "DCE", "m", "2027-01-15"),
    ]
    state_path = tmp_path / "state.json"
    broker = SimBroker(500000, specs, contract_catalog=catalog)
    manager = AutoPairManager(auto_config())
    engine = TradingEngine(
        broker,
        [],
        {},
        RiskManager(RiskConfig()),
        StateStore(state_path),
        auto_manager=manager,
        historical_mode=True,
    )
    engine.start()
    engine.on_tick(tick("m2609", 3100))
    engine.on_tick(tick("m2701", 3000))
    engine.stop()

    saved = StateStore(state_path).load()
    assert set(saved.auto_history) == {"m2609", "m2701"}

    restored_broker = SimBroker(500000, specs, contract_catalog=catalog)
    restored_manager = AutoPairManager(auto_config())
    restored_engine = TradingEngine(
        restored_broker,
        [],
        {},
        RiskManager(RiskConfig()),
        StateStore(state_path),
        auto_manager=restored_manager,
        historical_mode=True,
    )
    restored_engine.start()
    assert restored_manager.snapshot_history() == saved.auto_history
