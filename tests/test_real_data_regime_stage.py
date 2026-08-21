from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_REGIME_KEYS = (
    "min_persistence_score",
    "max_volatility_percentile",
    "max_trend_shift_z",
    "min_carry_reversal_z",
    "carry_reversal_weight",
)


def _research_module():
    path = Path(__file__).parents[1] / "tools" / "run_real_two_year_research.py"
    spec = spec_from_file_location("afuture_real_two_year_research", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quality_parameters():
    return {
        "lookback": 30,
        "entry_z": 1.5,
        "exit_z": 0.25,
        "min_net_edge": 50.0,
        "min_stationarity_score": 0.005,
        "max_half_life": 60.0,
        "risk_budget_ratio": 0.004,
        "max_pair_volume": 20,
    }


def test_regime_grid_is_small_single_axis_neighborhood():
    module = _research_module()
    rows = module.regime_grid(_quality_parameters())
    assert 8 <= len(rows) <= 16

    baseline = rows[0]
    assert baseline["min_persistence_score"] == 0.0
    assert baseline["max_volatility_percentile"] == 1.0
    assert baseline["max_trend_shift_z"] == 12.0
    assert baseline["min_carry_reversal_z"] == 0.0
    assert baseline["carry_reversal_weight"] == 0.0

    for row in rows[1:]:
        changed = [key for key in _REGIME_KEYS if row[key] != baseline[key]]
        assert len(changed) == 1, (changed, row)
        for key in ("lookback", "entry_z", "exit_z", "min_net_edge", "min_stationarity_score", "max_half_life"):
            assert row[key] == baseline[key]


def test_risk_grid_preserves_selected_regime_parameters():
    module = _research_module()
    selected = {
        **_quality_parameters(),
        "min_persistence_score": 0.34,
        "max_volatility_percentile": 0.95,
        "max_trend_shift_z": 4.0,
        "min_carry_reversal_z": 0.5,
        "carry_reversal_weight": 0.5,
    }
    rows = module.risk_grid(selected)
    assert rows
    for row in rows:
        for key in _REGIME_KEYS:
            assert row[key] == selected[key]


def test_regime_ablations_disable_exactly_one_selected_capability():
    module = _research_module()
    selected = {
        **_quality_parameters(),
        "min_persistence_score": 0.34,
        "max_volatility_percentile": 0.9,
        "max_trend_shift_z": 4.0,
        "min_carry_reversal_z": 0.5,
        "carry_reversal_weight": 0.5,
        "risk_budget_ratio": 0.008,
        "max_pair_volume": 12,
    }
    ablations = module.regime_ablations(selected)
    assert set(ablations) == {"persistence", "volatility", "trend_shift", "carry"}
    disabled = {
        "persistence": {"min_persistence_score": 0.0},
        "volatility": {"max_volatility_percentile": 1.0},
        "trend_shift": {"max_trend_shift_z": 12.0},
        "carry": {"min_carry_reversal_z": 0.0, "carry_reversal_weight": 0.0},
    }
    for name, row in ablations.items():
        expected = dict(selected)
        expected.update(disabled[name])
        assert row == expected
