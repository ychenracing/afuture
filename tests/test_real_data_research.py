from datetime import date
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
        # 当天 close/volume/OI 都是开盘时未知的信息，转换后不得进入当日决策。
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


def test_research_candidate_rejects_halt_open_positions_and_drawdown_limit():
    runner = _research_runner()
    safe = {
        "total_return": 0.10,
        "max_drawdown": -0.04,
        "sharpe": 1.2,
        "final_position_count": 0,
        "halted": False,
    }
    assert runner._metrics_acceptable(safe)
    assert not runner._metrics_acceptable({**safe, "halted": True})
    assert not runner._metrics_acceptable({**safe, "final_position_count": 1})
    assert not runner._metrics_acceptable({**safe, "max_drawdown": -0.08})


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
