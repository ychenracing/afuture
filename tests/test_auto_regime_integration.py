from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from afuture.auto import AutoConfig, AutoPairManager
from afuture.broker.sim import SimBroker
from afuture.models import ContractInfo, ContractSpec, Tick


_CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _tick(symbol: str, ts: datetime, mid: float) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=ts,
        bid_price=mid - 0.5,
        ask_price=mid + 0.5,
        last_price=mid,
        bid_volume=100,
        ask_volume=100,
        trading_day=ts.strftime("%Y%m%d"),
        volume=20000,
        open_interest=80000,
    )


def _specs():
    return {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }


def _catalog():
    return [
        ContractInfo("m2609", "DCE", "m", "2026-09-15"),
        ContractInfo("m2701", "DCE", "m", "2027-01-15"),
    ]


def _config(**overrides):
    values = dict(
        enabled=True,
        products=("m",),
        exchanges=("DCE",),
        max_active_pairs=1,
        max_contracts_per_product=2,
        min_days_to_expiry=0,
        scan_interval_seconds=0,
        lookback=6,
        entry_z=0.5,
        exit_z=0.1,
        stop_z=6.0,
        max_pair_volume=1,
        sample_seconds=0,
        min_volume=0,
        min_open_interest=0,
        min_liquidity_score=0,
        min_stationarity_score=0,
        max_half_life=1000,
        min_net_edge=0,
        session_windows=("09:00-11:30",),
    )
    values.update(overrides)
    return AutoConfig(**values)


def test_regime_config_validation_is_bounded():
    _config(min_persistence_score=0.5, max_volatility_percentile=0.9, max_trend_shift_z=3.0)
    with pytest.raises(ValueError):
        _config(min_persistence_score=1.1).validate()
    with pytest.raises(ValueError):
        _config(max_volatility_percentile=0).validate()
    with pytest.raises(ValueError):
        _config(carry_reversal_weight=-0.1).validate()


def test_persistence_gate_rejects_trending_pair_before_metadata_query():
    class CountingBroker(SimBroker):
        def __init__(self):
            super().__init__(500000, _specs(), contract_catalog=_catalog())
            self.queried = []

        def get_live_contract_specs(self, symbols, timeout_seconds=10.0):
            self.queried.extend(symbols)
            return super().get_live_contract_specs(symbols, timeout_seconds)

    broker = CountingBroker()
    broker.start()
    manager = AutoPairManager(_config(min_persistence_score=0.75))
    manager.bootstrap(broker, date(2026, 8, 21), {})
    start = datetime(2026, 8, 21, 9, 0, tzinfo=_CHINA_TZ)
    for index, spread in enumerate([10, 11, 12, 13, 14, 15, 20]):
        manager.observe(_tick("m2609", start + timedelta(minutes=index), 3000 + spread))
        manager.observe(_tick("m2701", start + timedelta(minutes=index), 3000))

    selected = manager.select(
        broker,
        now=start + timedelta(minutes=8),
        protected_pair_ids=set(),
    )
    manager.close()
    broker.stop()

    assert selected == []
    # Regime gate is part of the cheap statistical prefilter; rejected pairs must
    # not consume live CTP margin/commission query quota.
    assert broker.queried == []


def test_carry_reversal_gate_rejects_raw_spread_signal_with_conflicting_curve_shape():
    broker = SimBroker(500000, _specs(), contract_catalog=_catalog())
    broker.start()
    manager = AutoPairManager(
        _config(min_carry_reversal_z=0.5, carry_reversal_weight=1.0)
    )
    manager.bootstrap(broker, date(2026, 8, 21), {})
    start = datetime(2026, 8, 21, 9, 0, tzinfo=_CHINA_TZ)
    history = [
        (100, 90),
        (101, 91),
        (99, 89),
        (100, 90),
        (101, 91),
        (99, 89),
        # Raw spread widens from ~10 to 15 -> SHORT signal, but normalized
        # near/far ratio becomes cheaper than history, which contradicts it.
        (200, 185),
    ]
    for index, (near, far) in enumerate(history):
        manager.observe(_tick("m2609", start + timedelta(minutes=index), near))
        manager.observe(_tick("m2701", start + timedelta(minutes=index), far))

    selected = manager.select(
        broker,
        now=start + timedelta(minutes=8),
        protected_pair_ids=set(),
    )
    manager.close()
    broker.stop()

    assert selected == []
