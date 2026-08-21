from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


def _setup(latency_ticks: int = 0):
    from afuture.broker.sim import SimBroker
    from afuture.engine import TradingEngine
    from afuture.models import ContractSpec, PairConfig, Tick
    from afuture.risk import RiskConfig, RiskManager
    from afuture.state import StateStore

    specs = {
        "N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15),
        "F": ContractSpec("F", "DCE", 10, 1, 0.15, 0.15),
    }
    pair = PairConfig("p", "N", "F", "DCE", 1)
    broker = SimBroker(
        500000,
        specs,
        conservative=True,
        latency_ticks=latency_ticks,
    )
    temp = TemporaryDirectory(prefix="afuture-terminal-boundary-")
    engine = TradingEngine(
        broker,
        [pair],
        specs,
        RiskManager(RiskConfig(max_orders_per_minute=100)),
        StateStore(Path(temp.name) / "state.json"),
        historical_mode=True,
    )
    engine.start()
    timestamp = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    near = Tick("N", "DCE", timestamp, 99, 100, 99.5, 100, 100, "20260820")
    far = Tick("F", "DCE", timestamp, 49, 50, 49.5, 100, 100, "20260820")
    broker.publish_tick(near)
    broker.publish_tick(far)
    broker.poll_events()
    engine.quotes.update({"N": near, "F": far})
    return temp, engine, broker, pair, near, far


def test_terminal_liquidation_cancels_unfilled_orders_before_closing():
    from afuture.auto_research import AutoPortfolioRunner
    from afuture.models import Offset, OrderRequest, OrderSide, OrderType

    temp, engine, broker, _pair, _near, _far = _setup(latency_ticks=2)
    try:
        broker.send_order(
            OrderRequest("N", "DCE", OrderSide.BUY, Offset.OPEN, 1, 100, OrderType.FAK, "p")
        )
        assert broker.get_active_orders()
        assert broker.get_positions() == []

        success, reason = AutoPortfolioRunner._terminal_liquidate(
            engine, broker, "20260820"
        )
        assert success, reason
        assert broker.get_active_orders() == []
        assert broker.get_positions() == []
    finally:
        engine.stop()
        temp.cleanup()


def test_terminal_liquidation_does_not_clone_future_quotes_for_latency():
    from afuture.auto_research import AutoPortfolioRunner
    from afuture.models import Offset, OrderRequest, OrderSide, OrderType

    temp, engine, broker, _pair, near, far = _setup(latency_ticks=0)
    try:
        broker.send_order(
            OrderRequest("N", "DCE", OrderSide.BUY, Offset.OPEN, 1, 100, OrderType.FAK, "p")
        )
        broker.send_order(
            OrderRequest("F", "DCE", OrderSide.SELL, Offset.OPEN, 1, 49, OrderType.FAK, "p")
        )
        assert broker.get_positions()
        broker.latency_ticks = 2
        engine.quotes.update({"N": near, "F": far})

        success, reason = AutoPortfolioRunner._terminal_liquidate(
            engine, broker, "20260820"
        )
        assert not success
        assert "future quote" in reason
        assert broker.get_positions()
    finally:
        engine.stop()
        temp.cleanup()
