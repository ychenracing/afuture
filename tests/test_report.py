from afuture.report import calculate_performance


def test_performance_metrics_include_return_drawdown_and_sharpe():
    metrics = calculate_performance(
        [("20260817", 100000.0), ("20260818", 110000.0), ("20260819", 99000.0)],
        initial_capital=100000.0,
        trade_count=4,
    )
    assert round(metrics["total_return"], 4) == -0.01
    assert round(metrics["max_drawdown"], 4) == -0.10
    assert metrics["trade_count"] == 4
    assert "sharpe" in metrics
