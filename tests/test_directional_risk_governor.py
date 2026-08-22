from afuture.directional_risk import DirectionalRiskGovernor
from afuture.state import RuntimeState, StateStore


def test_directional_risk_governor_uses_only_completed_returns_and_scales_defensively():
    governor = DirectionalRiskGovernor()

    assert governor.scale([]) == 1.0
    assert governor.scale([-0.02, 0.01]) == 1.0
    assert governor.scale([-0.031]) == 0.25
    assert governor.scale([-0.04, 0.01]) == 0.25


def test_directional_risk_governor_keeps_scale_inside_one_and_never_increases_gross():
    governor = DirectionalRiskGovernor()
    for returns in ([], [0.10], [0.10, -0.10], [-0.50, 0.50]):
        scale = governor.scale(returns)
        assert 0.0 < scale <= 1.0


def test_runtime_state_persists_completed_directional_return_history_and_daily_circuit(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = RuntimeState(
        recent_daily_returns=[-0.031, 0.012],
        directional_daily_circuit_day="20260825",
        last_account_equity=96900.0,
        last_account_trading_day="20260825",
    )
    store.save(state)

    restored = store.load()
    assert restored.recent_daily_returns == [-0.031, 0.012]
    assert restored.directional_daily_circuit_day == "20260825"
    assert restored.last_account_equity == 96900.0
    assert restored.last_account_trading_day == "20260825"
