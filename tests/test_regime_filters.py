from afuture.regime import evaluate_entry_regime


def test_persistence_rewards_repeated_mean_reversion_not_one_lucky_window():
    stable = [10, 11, 9, 10, 11, 9, 10, 10.5, 9.5, 10, 11, 9, 10]
    trending = list(range(10, 23))

    stable_metrics = evaluate_entry_regime(stable, stable)
    trend_metrics = evaluate_entry_regime(trending, trending)

    assert stable_metrics.persistence_score > trend_metrics.persistence_score
    assert 0.0 <= stable_metrics.persistence_score <= 1.0


def test_volatility_percentile_flags_recent_shock():
    quiet = [10, 10.2, 9.8, 10.1, 9.9, 10.0, 10.2, 9.8, 10.1, 9.9, 10.0, 10.1]
    shocked = quiet + [15.0, 5.0, 16.0]

    quiet_metrics = evaluate_entry_regime(quiet, quiet)
    shocked_metrics = evaluate_entry_regime(shocked, shocked)

    assert shocked_metrics.volatility_percentile > quiet_metrics.volatility_percentile
    assert shocked_metrics.volatility_percentile >= 0.8


def test_changepoint_gate_detects_slow_level_shift():
    stable = [10, 10.2, 9.8, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0, 10.1, 9.9, 10.0]
    shifted = [10, 10.2, 9.8, 10.1, 9.9, 10.0, 13, 13.2, 12.8, 13.1, 13.0, 13.2]

    stable_metrics = evaluate_entry_regime(stable, stable)
    shifted_metrics = evaluate_entry_regime(shifted, shifted)

    assert shifted_metrics.trend_shift_z > stable_metrics.trend_shift_z
    assert shifted_metrics.trend_shift_z > 1.0


def test_normalized_curve_carry_reversal_is_scale_invariant():
    # Same percentage curve shape at 100/90 and 1000/900 should produce
    # essentially the same normalized carry signal.
    near = [100, 101, 99, 100, 101, 99, 100, 110]
    far = [90, 90.9, 89.1, 90, 90.9, 89.1, 90, 90]
    near_scaled = [value * 10 for value in near]
    far_scaled = [value * 10 for value in far]

    base = evaluate_entry_regime(
        [n - f for n, f in zip(near, far)],
        [n / f for n, f in zip(near, far)],
    )
    scaled = evaluate_entry_regime(
        [n - f for n, f in zip(near_scaled, far_scaled)],
        [n / f for n, f in zip(near_scaled, far_scaled)],
    )

    assert base.carry_z > 0
    assert abs(base.carry_z - scaled.carry_z) < 1e-12


def test_entry_gate_combines_persistence_volatility_change_and_carry_direction():
    spreads = [10, 11, 9, 10, 11, 9, 10, 10.5, 9.5, 10, 11, 9, 14]
    carries = [1.00, 1.01, 0.99, 1.00, 1.01, 0.99, 1.00, 1.005, 0.995, 1.00, 1.01, 0.99, 1.04]
    metrics = evaluate_entry_regime(spreads, carries)

    assert metrics.carry_z > 0
    # Positive carry deviation supports fading an expensive near leg (SHORT spread).
    assert metrics.supports_short
    assert not metrics.supports_long
