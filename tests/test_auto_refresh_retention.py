from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


def test_engine_catalog_refresh_retains_only_open_auto_pairs():
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
    broker = SimBroker(500000, specs)
    with TemporaryDirectory(prefix="afuture-refresh-retention-") as temp:
        engine = TradingEngine(
            broker,
            [old_pair, open_pair],
            specs,
            RiskManager(RiskConfig()),
            StateStore(Path(temp) / "state.json"),
            auto_manager=manager,
            historical_mode=True,
        )
        engine._auto_pair_ids = {"old", "open"}
        engine._open_auto_pair_ids = lambda: {"open"}
        engine._trading_date = lambda: date(2026, 8, 21)

        engine._refresh_auto_pairs(datetime(2026, 8, 21, tzinfo=timezone.utc))

    assert manager.retained == [open_pair]
