import pytest

from afuture.curve_research import (
    CurveFamilyConfig,
    CurveFamilyResearch,
    CurveObservation,
)


def _rows(prefix: str, near_prices: list[float], far_prices: list[float]):
    return [
        CurveObservation(
            trading_day=f"202601{index + 1:02d}",
            near_symbol=f"{prefix}N",
            far_symbol=f"{prefix}F",
            near_price=float(near),
            far_price=float(far),
        )
        for index, (near, far) in enumerate(zip(near_prices, far_prices))
    ]


def test_curve_portfolio_keeps_missing_product_as_cash_sleeve():
    research = CurveFamilyResearch.__new__(CurveFamilyResearch)
    research.specs = {}
    active = _rows("A", [100, 101, 102, 103, 104, 105], [100] * 6)
    # 第二个品种只有一个观察点，研究期内不产生收益；它仍代表固定的一半资本 sleeve，
    # 不能因为没有当日 return row 就把第一品种临时放大到 100% 权重。
    inactive = _rows("B", [100], [100])
    research._series = {"a": active, "b": inactive}

    config = CurveFamilyConfig(
        "basis_momentum",
        fast_window=2,
        slow_window=2,
        mean_window=3,
        rebalance_samples=1,
        slippage_ticks=0,
    )
    active_returns, _ = research._run_product(active, config, None)
    expected = research._compound([value / 2.0 for value in active_returns.values()])

    metrics = research.run(config)
    assert metrics["total_return"] == pytest.approx(expected)
