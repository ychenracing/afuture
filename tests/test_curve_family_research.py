import pytest

from afuture.curve_research import (
    CurveFamilyConfig,
    CurveFamilyResearch,
    CurveObservation,
)
from afuture.models import ContractSpec, FeeSpec


def _rows(near, far):
    return [
        CurveObservation(
            trading_day=f"202601{index + 1:02d}",
            near_symbol="N",
            far_symbol="F",
            near_price=float(n),
            far_price=float(f),
        )
        for index, (n, f) in enumerate(zip(near, far))
    ]


def _research():
    return CurveFamilyResearch.__new__(CurveFamilyResearch)


def _spec(symbol: str) -> ContractSpec:
    return ContractSpec(symbol, "DCE", 10, 1, 0.1, 0.1)


def test_basis_reversal_trades_against_recent_relative_move():
    rows = _rows([100, 101, 102, 103, 104, 110, 112], [100] * 7)
    config = CurveFamilyConfig(
        "basis_reversal",
        fast_window=5,
        slow_window=5,
        mean_window=5,
    )
    assert _research()._desired_position(rows, 6, 0, config) == -1


def test_basis_momentum_follows_slow_relative_move():
    rows = _rows([100, 101, 102, 103, 104, 105, 106], [100] * 7)
    config = CurveFamilyConfig(
        "basis_momentum",
        fast_window=2,
        slow_window=5,
        mean_window=5,
    )
    assert _research()._desired_position(rows, 6, 0, config) == 1


def test_slow_momentum_fast_reversion_temporarily_follows_local_break():
    near = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 104, 100, 98]
    far = [100] * len(near)
    rows = _rows(near, far)
    config = CurveFamilyConfig(
        "slow_momentum_fast_reversion",
        fast_window=2,
        slow_window=10,
        mean_window=5,
        change_severity=0.5,
    )
    assert _research()._desired_position(rows, len(rows) - 1, 0, config) == -1


def test_minimum_volatility_percentile_can_block_dead_curve():
    rows = _rows(
        [100 + (0.01 if index % 2 else 0) for index in range(20)],
        [100] * 20,
    )
    config = CurveFamilyConfig(
        "basis_reversal",
        fast_window=3,
        slow_window=6,
        mean_window=6,
        min_volatility_percentile=0.8,
    )
    assert _research()._desired_position(rows, len(rows) - 1, 0, config) == 0


def test_pair_roll_does_not_create_artificial_return():
    research = _research()
    research.specs = {}
    rows = [
        CurveObservation("20260101", "N1", "F1", 100, 90),
        CurveObservation("20260102", "N1", "F1", 101, 90),
        CurveObservation("20260103", "N2", "F2", 500, 400),
        CurveObservation("20260104", "N2", "F2", 501, 400),
    ]
    config = CurveFamilyConfig(
        "basis_reversal",
        fast_window=2,
        slow_window=2,
        mean_window=2,
        slippage_ticks=0,
    )
    returns, _ = research._run_product(rows, config, None)
    assert "20260103" not in returns


def test_basis_momentum_uses_roll_adjusted_role_index_history():
    # Raw contract prices jump at the role switch, but the role indices preserve
    # only daily returns of whichever contract is F1/F2 on each date.
    rows = [
        CurveObservation("20260101", "N1", "F1", 100, 90, 1.00, 1.00),
        CurveObservation("20260102", "N1", "F1", 101, 90, 1.01, 1.00),
        CurveObservation("20260103", "N1", "F1", 102, 90, 1.02, 1.00),
        CurveObservation("20260104", "N2", "F2", 500, 400, 1.03, 1.00),
        CurveObservation("20260105", "N2", "F2", 505, 400, 1.04, 1.00),
        CurveObservation("20260106", "N2", "F2", 510, 400, 1.05, 1.00),
    ]
    config = CurveFamilyConfig(
        "basis_momentum",
        fast_window=2,
        slow_window=5,
        mean_window=5,
    )
    assert _research()._desired_position(rows, 5, 0, config) == 1


def test_curve_pnl_uses_equal_lot_cash_return_not_equal_notional_percent_return():
    research = _research()
    research.specs = {
        "N": ContractSpec("N", "DCE", 10, 1, 0.1, 0.1),
        "F": ContractSpec("F", "DCE", 10, 1, 0.1, 0.1),
    }
    rows = [
        CurveObservation("20260101", "N", "F", 100, 50, 1.00, 1.00),
        CurveObservation("20260102", "N", "F", 101, 50, 1.01, 1.00),
        CurveObservation("20260103", "N", "F", 102, 50, 1.02, 1.00),
        CurveObservation("20260104", "N", "F", 103, 50, 1.03, 1.00),
    ]
    config = CurveFamilyConfig(
        "basis_momentum",
        fast_window=2,
        slow_window=2,
        mean_window=3,
        rebalance_samples=1,
        slippage_ticks=0,
    )
    returns, _ = research._run_product(rows, config, None)
    expected = (103 - 102) * 10 / (102 * 10 + 50 * 10)
    assert returns["20260104"] == pytest.approx(expected)


def test_pair_roll_charges_exit_cost_without_counting_roll_jump():
    research = _research()
    research.specs = {symbol: _spec(symbol) for symbol in ("N1", "F1", "N2", "F2")}
    rows = [
        CurveObservation("20260101", "N1", "F1", 100, 100, 1.00, 1.00),
        CurveObservation("20260102", "N1", "F1", 101, 100, 1.01, 1.00),
        CurveObservation("20260103", "N1", "F1", 102, 100, 1.02, 1.00),
        CurveObservation("20260104", "N1", "F1", 103, 100, 1.03, 1.00),
        CurveObservation("20260105", "N1", "F1", 104, 100, 1.04, 1.00),
        CurveObservation("20260106", "N2", "F2", 500, 400, 1.05, 1.00),
    ]
    config = CurveFamilyConfig(
        "basis_momentum",
        fast_window=2,
        slow_window=3,
        mean_window=3,
        rebalance_samples=1,
        slippage_ticks=1,
    )
    returns, trades = research._run_product(rows, config, None)
    close_cost = research._transaction_cost(rows[4], 1, 1)
    gross = (104 - 103) * 10 / (103 * 10 + 100 * 10)
    assert returns["20260105"] == pytest.approx(gross - close_cost)
    assert "20260106" not in returns
    assert trades >= 2


def test_terminal_position_is_closed_with_cost():
    research = _research()
    research.specs = {symbol: _spec(symbol) for symbol in ("N", "F")}
    rows = [
        CurveObservation("20260101", "N", "F", 100, 100, 1.00, 1.00),
        CurveObservation("20260102", "N", "F", 101, 100, 1.01, 1.00),
        CurveObservation("20260103", "N", "F", 102, 100, 1.02, 1.00),
        CurveObservation("20260104", "N", "F", 103, 100, 1.03, 1.00),
        CurveObservation("20260105", "N", "F", 104, 100, 1.04, 1.00),
        CurveObservation("20260106", "N", "F", 105, 100, 1.05, 1.00),
    ]
    config = CurveFamilyConfig(
        "basis_momentum",
        fast_window=2,
        slow_window=3,
        mean_window=3,
        rebalance_samples=1,
        slippage_ticks=1,
    )
    returns, trades = research._run_product(rows, config, None)
    close_cost = research._transaction_cost(rows[-1], 1, 1)
    gross = (105 - 104) * 10 / (104 * 10 + 100 * 10)
    assert returns["20260106"] == pytest.approx(gross - close_cost)
    assert trades >= 2


def test_curve_fee_cost_uses_cash_fee_over_combined_notional():
    research = _research()
    research.specs = {
        "N": ContractSpec(
            "N", "DCE", 10, 1, 0.1, 0.1,
            FeeSpec(open_fixed=5.0, close_fixed=5.0),
        ),
        "F": ContractSpec("F", "DCE", 10, 1, 0.1, 0.1),
    }
    row = CurveObservation("20260101", "N", "F", 100, 50)
    cost = research._transaction_cost(row, 1, 0)
    # A single turnover is one side of a round trip: half of the near leg's
    # 10-yuan open+close schedule, normalized by both legs' gross notional.
    assert cost == pytest.approx(5.0 / (100 * 10 + 50 * 10))
