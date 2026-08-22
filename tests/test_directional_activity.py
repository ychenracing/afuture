from datetime import date, datetime, timezone

from afuture.directional import DirectionalConfig, DirectionalContractSelector
from afuture.directional_activity import (
    DirectionalActivityStore,
    DirectionalActivityTracker,
)
from afuture.models import ContractInfo, Tick


def _tick(symbol: str, trading_day: str, *, volume: float, oi: float) -> Tick:
    return Tick(
        symbol=symbol,
        exchange="DCE",
        timestamp=datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc),
        bid_price=99.0,
        ask_price=101.0,
        last_price=100.0,
        bid_volume=1000,
        ask_volume=1000,
        volume=volume,
        open_interest=oi,
        trading_day=trading_day,
        limit_up=120.0,
        limit_down=80.0,
    )


def _catalog():
    return {
        "A2609": ContractInfo("A2609", "DCE", "A", "2026-09-15"),
        "A2611": ContractInfo("A2611", "DCE", "A", "2026-11-15"),
    }


def test_activity_tracker_freezes_previous_trading_day_and_reloads(tmp_path):
    store = DirectionalActivityStore(tmp_path / "directional_activity.json")
    tracker = DirectionalActivityTracker(store)
    catalog = _catalog()

    tracker.observe(_tick("A2609", "20260821", volume=8000, oi=30000), catalog["A2609"])
    tracker.observe(_tick("A2611", "20260821", volume=12000, oi=20000), catalog["A2611"])
    assert tracker.completed_snapshot is None

    # The first tick of the next CTP trading day freezes all latest observations from D.
    tracker.observe(_tick("A2611", "20260825", volume=1, oi=99999), catalog["A2611"])
    snapshot = tracker.completed_snapshot
    assert snapshot is not None
    assert snapshot.trading_day == "20260821"
    assert snapshot.contracts["A2609"].open_interest == 30000
    assert snapshot.contracts["A2611"].volume == 12000

    restored = DirectionalActivityTracker(store)
    assert restored.completed_snapshot == snapshot


def test_previous_completed_activity_controls_contract_selection_not_current_ticks(tmp_path):
    store = DirectionalActivityStore(tmp_path / "directional_activity.json")
    tracker = DirectionalActivityTracker(store)
    catalog = _catalog()
    tracker.observe(_tick("A2609", "20260821", volume=20000, oi=50000), catalog["A2609"])
    tracker.observe(_tick("A2611", "20260821", volume=30000, oi=30000), catalog["A2611"])
    tracker.observe(_tick("A2611", "20260825", volume=500000, oi=999999), catalog["A2611"])

    selector = DirectionalContractSelector(
        DirectionalConfig(
            enabled=True,
            products=("A",),
            exchanges=("DCE",),
            min_days_to_expiry=20,
            min_volume=1000,
            min_open_interest=5000,
        )
    )
    selected = selector.select_from_activity(
        list(catalog.values()),
        tracker.completed_snapshot,
        date(2026, 8, 25),
    )
    assert selected["A"].symbol == "A2609"
