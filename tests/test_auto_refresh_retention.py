from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


def _engine_with_manager(manager):
    from afuture.broker.sim import SimBroker
    from afuture.engine import TradingEngine
    from afuture.models import ContractSpec, PairConfig
    from afuture.risk import RiskConfig, RiskManager
    from afuture.state import StateStore

    specs = {
        "N1": ContractSpec("N1", "DCE", 10, 1, 0.15, 0.15),
        "F1": ContractSpec("F1", "DCE", 10, 1, 0.15, 0.15),
        "N2": ContractSpec("N2", "DCE", 10, 1, 0.15, 0.15),
        "F2": ContractSpec("F2", "DCE", 10, 1, 0.15, 0.15),
    }
    old_pair = PairConfig("old", "N1", "F1", "DCE", 1, risk_group="m")
    open_pair = PairConfig("open", "N2", "F2", "DCE", 1, risk_group="m")
    broker = SimBroker(500000, specs)
    temp = TemporaryDirectory(prefix="afuture-refresh-retention-")
    engine = TradingEngine(
        broker,
        [old_pair, open_pair],
        specs,
        RiskManager(RiskConfig()),
        StateStore(Path(temp.name) / "state.json"),
        auto_manager=manager,
        historical_mode=True,
    )
    engine._auto_pair_ids = {"old", "open"}
    engine._open_auto_pair_ids = lambda: {"open"}
    engine._trading_date = lambda: date(2026, 8, 21)
    return temp, engine, old_pair, open_pair


def test_engine_catalog_refresh_retains_only_open_auto_pairs():
    class RecordingManager:
        initialized = True
        last_eligible_ids = set()

        def __init__(self):
            self.retained = None

        def refresh_if_needed(self, broker, today, *, retained_pairs=()):
            self.retained = list(retained_pairs)
            return True

        def select(self, broker, *, now, protected_pair_ids):
            return None

    manager = RecordingManager()
    temp, engine, _old_pair, open_pair = _engine_with_manager(manager)
    try:
        engine._refresh_auto_pairs(datetime(2026, 8, 21, tzinfo=timezone.utc))
    finally:
        temp.cleanup()

    assert manager.retained == [open_pair]


def test_engine_catalog_refresh_failure_retires_unprotected_pairs():
    class FailingManager:
        initialized = True
        last_eligible_ids = set()

        def refresh_if_needed(self, broker, today, *, retained_pairs=()):
            raise RuntimeError("catalog unavailable")

        def select(self, broker, *, now, protected_pair_ids):
            raise AssertionError("select should not run after catalog refresh failure")

    temp, engine, _old_pair, _open_pair = _engine_with_manager(FailingManager())
    try:
        engine._refresh_auto_pairs(datetime(2026, 8, 21, tzinfo=timezone.utc))
    finally:
        temp.cleanup()

    assert engine._retiring_auto_pairs == {"old"}
