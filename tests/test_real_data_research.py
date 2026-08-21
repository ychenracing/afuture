from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest


def test_sina_daily_parser_preserves_real_fields():
    from afuture.real_data import parse_sina_daily_jsonp

    payload = 'var x=([["2026-08-20","3000","3020","2990","3010","12345","67890","3008"]]);'
    rows = parse_sina_daily_jsonp(payload, "M2609")
    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "M2609"
    assert row.day == date(2026, 8, 20)
    assert row.close == pytest.approx(3010)
    assert row.volume == pytest.approx(12345)
    assert row.open_interest == pytest.approx(67890)
    assert row.settle == pytest.approx(3008)


def test_sina_client_accepts_valid_empty_response_without_retry():
    from afuture.real_data import SinaDailyClient

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"var x=([]);"

    with patch("afuture.real_data.urlopen", return_value=Response()) as mocked:
        rows = SinaDailyClient(timeout_seconds=1.0, retries=3).fetch("M9999")
    assert rows == []
    assert mocked.call_count == 1


def test_daily_bar_conversion_uses_open_and_lagged_activity_without_lookahead():
    from afuture.real_data import DailyBar, ProductDefinition, daily_bars_to_ticks

    definition = ProductDefinition(
        product="m",
        exchange="DCE",
        multiplier=10,
        price_tick=1,
        margin_rate=0.15,
        open_fee=2.0,
        close_fee=2.0,
        contract_months=(1, 5, 9),
    )
    rows = [
        DailyBar("M2609", date(2026, 8, 19), 2990, 3010, 2980, 3000, 100000, 200000, 2998),
        DailyBar("M2609", date(2026, 8, 20), 3000, 3020, 2990, 3010, 999999, 888888, 3008),
    ]
    result = daily_bars_to_ticks(rows, definition)
    assert result.execution_proxy == (
        "open +/- one price tick; prior-day volume/open_interest; historical L1 unavailable"
    )
    assert len(result.ticks) == 1
    current = result.ticks[0]
    assert current.last_price == pytest.approx(3000)
    assert current.bid_price == pytest.approx(2999)
    assert current.ask_price == pytest.approx(3001)
    assert current.volume == pytest.approx(100000)
    assert current.open_interest == pytest.approx(200000)


def test_auto_selector_excludes_contract_before_historical_listing_date():
    from afuture.auto import AutoConfig, AutoPairSelector
    from afuture.models import ContractInfo

    selector = AutoPairSelector(
        AutoConfig(
            enabled=True,
            products=("m",),
            exchanges=("DCE",),
            max_contracts_per_product=3,
            min_days_to_expiry=0,
        )
    )
    catalog = [
        ContractInfo("M2505", "DCE", "m", "2025-05-20", listing="2024-01-01"),
        ContractInfo("M2509", "DCE", "m", "2025-09-20", listing="2024-06-01"),
        ContractInfo("M2601", "DCE", "m", "2026-01-20", listing="2025-03-01"),
    ]
    before = selector.build_pairs(catalog, date(2025, 2, 1))
    assert [(row.near_symbol, row.far_symbol) for row in before] == [("M2505", "M2509")]
    after = selector.build_pairs(catalog, date(2025, 4, 1))
    assert [(row.near_symbol, row.far_symbol) for row in after] == [
        ("M2505", "M2509"),
        ("M2509", "M2601"),
    ]


def test_historical_auto_manager_resolves_simbroker_specs_on_first_scan():
    from afuture.auto import AutoConfig, AutoPairManager
    from afuture.broker.sim import SimBroker
    from afuture.models import ContractInfo, ContractSpec

    config = AutoConfig(
        enabled=True,
        products=("m",),
        exchanges=("DCE",),
        max_contracts_per_product=2,
        min_days_to_expiry=0,
    )
    specs = {
        "M2505": ContractSpec("M2505", "DCE", 10, 1, 0.15, 0.15),
        "M2509": ContractSpec("M2509", "DCE", 10, 1, 0.15, 0.15),
    }
    catalog = [
        ContractInfo("M2505", "DCE", "m", "2025-05-20", listing="2024-01-01"),
        ContractInfo("M2509", "DCE", "m", "2025-09-20", listing="2024-01-01"),
    ]
    broker = SimBroker(500000, specs, contract_catalog=catalog)
    broker.start()
    manager = AutoPairManager(config)
    pair = manager.selector.build_pairs(catalog, date(2025, 1, 1))[0]
    assert manager._prefetched_specs(broker, pair) == specs
    manager.close()
    broker.stop()


def _research_runner():
    from dataclasses import replace
    from afuture.auto import AutoConfig
    from afuture.auto_research import AutoPortfolioRunner
    from afuture.config import AppConfig
    from afuture.risk import RiskConfig

    base = AppConfig(
        mode="replay",
        initial_capital=500000,
        contracts={},
        pairs=[],
        risk=RiskConfig(risk_budget_ratio=0.002, max_total_drawdown_ratio=0.08),
        ctp=None,
        auto=replace(AutoConfig(), enabled=True, products=("m",)),
        contract_catalog=[],
    )
    return AutoPortfolioRunner(base)


def test_research_parameter_application_supports_bounded_risk_scaling():
    runner = _research_runner()
    tuned = runner._config_with_parameters({"risk_budget_ratio": 0.01, "max_pair_volume": 8})
    assert tuned.risk.risk_budget_ratio == pytest.approx(0.01)
    assert tuned.risk.max_total_drawdown_ratio == pytest.approx(0.08)
    assert tuned.auto.max_pair_volume == 8
    with pytest.raises(ValueError):
        runner._config_with_parameters({"risk_budget_ratio": 0.09})


def test_research_candidate_rejects_unsafe_or_insufficient_evidence():
    runner = _research_runner()
    safe = {
        "total_return": 0.10,
        "max_drawdown": -0.04,
        "sharpe": 1.2,
        "trade_count": 2,
        "final_position_count": 0,
        "halted": False,
    }
    assert runner._metrics_acceptable(safe, min_trades=1)
    assert not runner._metrics_acceptable({**safe, "halted": True}, min_trades=1)
    assert not runner._metrics_acceptable({**safe, "final_position_count": 1}, min_trades=1)
    assert not runner._metrics_acceptable({**safe, "max_drawdown": -0.08}, min_trades=1)
    assert not runner._metrics_acceptable({**safe, "trade_count": 0}, min_trades=1)


def test_research_terminal_liquidation_closes_current_day_positions():
    from afuture.auto_research import AutoPortfolioRunner
    from afuture.broker.sim import SimBroker
    from afuture.engine import TradingEngine
    from afuture.models import (
        ContractSpec,
        Offset,
        OrderRequest,
        OrderSide,
        OrderType,
        PairConfig,
        Tick,
    )
    from afuture.risk import RiskConfig, RiskManager
    from afuture.state import StateStore

    specs = {
        "N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15),
        "F": ContractSpec("F", "DCE", 10, 1, 0.15, 0.15),
    }
    pair = PairConfig("p", "N", "F", "DCE", 1)
    broker = SimBroker(500000, specs, conservative=True)
    with TemporaryDirectory(prefix="afuture-terminal-test-") as temp:
        engine = TradingEngine(
            broker,
            [pair],
            specs,
            RiskManager(RiskConfig(max_orders_per_minute=100)),
            StateStore(Path(temp) / "state.json"),
            historical_mode=True,
        )
        engine.start()
        timestamp = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
        near = Tick("N", "DCE", timestamp, 99, 100, 99.5, 100, 100, "20260820")
        far = Tick("F", "DCE", timestamp, 49, 50, 49.5, 100, 100, "20260820")
        broker.publish_tick(near)
        broker.publish_tick(far)
        broker.poll_events()
        broker.send_order(OrderRequest("N", "DCE", OrderSide.BUY, Offset.OPEN, 1, 100, OrderType.FAK, "p"))
        broker.send_order(OrderRequest("F", "DCE", OrderSide.SELL, Offset.OPEN, 1, 49, OrderType.FAK, "p"))
        assert broker.get_positions()
        engine.quotes.update({"N": near, "F": far})

        success, reason = AutoPortfolioRunner._terminal_liquidate(
            engine, broker, "20260820"
        )
        assert success, reason
        assert broker.get_positions() == []
        assert len(broker.get_trades()) == 4
        engine.stop()


def test_research_terminal_liquidation_rejects_stale_quotes():
    from afuture.auto_research import AutoPortfolioRunner
    from afuture.broker.sim import SimBroker
    from afuture.engine import TradingEngine
    from afuture.models import (
        ContractSpec,
        Offset,
        OrderRequest,
        OrderSide,
        OrderType,
        PairConfig,
        Tick,
    )
    from afuture.risk import RiskConfig, RiskManager
    from afuture.state import StateStore

    specs = {
        "N": ContractSpec("N", "DCE", 10, 1, 0.15, 0.15),
        "F": ContractSpec("F", "DCE", 10, 1, 0.15, 0.15),
    }
    pair = PairConfig("p", "N", "F", "DCE", 1)
    broker = SimBroker(500000, specs, conservative=True)
    with TemporaryDirectory(prefix="afuture-terminal-stale-test-") as temp:
        engine = TradingEngine(
            broker,
            [pair],
            specs,
            RiskManager(RiskConfig(max_orders_per_minute=100)),
            StateStore(Path(temp) / "state.json"),
            historical_mode=True,
        )
        engine.start()
        timestamp = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
        near = Tick("N", "DCE", timestamp, 99, 100, 99.5, 100, 100, "20260819")
        far = Tick("F", "DCE", timestamp, 49, 50, 49.5, 100, 100, "20260819")
        broker.publish_tick(near)
        broker.publish_tick(far)
        broker.poll_events()
        broker.send_order(OrderRequest("N", "DCE", OrderSide.BUY, Offset.OPEN, 1, 100, OrderType.FAK, "p"))
        broker.send_order(OrderRequest("F", "DCE", OrderSide.SELL, Offset.OPEN, 1, 49, OrderType.FAK, "p"))
        engine.quotes.update({"N": near, "F": far})

        success, reason = AutoPortfolioRunner._terminal_liquidate(
            engine, broker, "20260820"
        )
        assert not success
        assert "terminal quote is not from final trading day" in reason
        assert broker.get_positions()
        engine.stop()


def test_research_search_stage_can_skip_post_analysis():
    from afuture.auto_research import AutoPortfolioResearchConfig

    runner = _research_runner()
    result = runner.run(
        [],
        AutoPortfolioResearchConfig(
            train_days=1,
            validation_days=1,
            oos_days=1,
            step_days=1,
            run_post_analysis=False,
        ),
    )
    assert result.stress_results == {}
    assert result.robustness == {}
