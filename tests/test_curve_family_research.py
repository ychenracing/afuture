from afuture.curve_research import (
    CurveFamilyConfig,
    CurveFamilyResearch,
    CurveObservation,
)


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
