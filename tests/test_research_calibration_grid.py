from afuture.calibration import ParameterCalibrator


def test_grid_adjacency_uses_quality_and_risk_dimensions():
    rows = [
        {
            "lookback": 30,
            "entry_z": 2.0,
            "exit_z": 0.25,
            "min_net_edge": 0.0,
            "max_half_life": 30.0,
            "risk_budget_ratio": 0.002,
            "max_pair_volume": 5,
            "score": 10.0,
            "max_drawdown": 0.01,
        },
        {
            "lookback": 30,
            "entry_z": 2.0,
            "exit_z": 0.25,
            "min_net_edge": 50.0,
            "max_half_life": 30.0,
            "risk_budget_ratio": 0.002,
            "max_pair_volume": 5,
            "score": 3.0,
            "max_drawdown": 0.01,
        },
        {
            "lookback": 30,
            "entry_z": 2.0,
            "exit_z": 0.25,
            "min_net_edge": 100.0,
            "max_half_life": 30.0,
            "risk_budget_ratio": 0.002,
            "max_pair_volume": 5,
            "score": 3.0,
            "max_drawdown": 0.01,
        },
    ]
    calibrator = ParameterCalibrator(min_neighbors=2)
    selected = calibrator.select_best(
        rows,
        parameter_keys=(
            "lookback",
            "entry_z",
            "exit_z",
            "min_net_edge",
            "max_half_life",
            "risk_budget_ratio",
            "max_pair_volume",
        ),
        grid_adjacency=True,
    )
    # The isolated zero-edge spike must not win merely because all rows share
    # the signal parameters. The stable 50/100 edge neighborhood should win.
    assert selected is not None
    assert selected["min_net_edge"] in {50.0, 100.0}


def test_grid_adjacency_allows_one_axis_step_but_not_diagonal_jump():
    calibrator = ParameterCalibrator(min_neighbors=2)
    rows = [
        {"entry_z": 1.5, "risk_budget_ratio": 0.002, "score": 1.0},
        {"entry_z": 1.8, "risk_budget_ratio": 0.002, "score": 1.0},
        {"entry_z": 1.8, "risk_budget_ratio": 0.004, "score": 9.0},
    ]
    selected = calibrator.select_best(
        rows,
        parameter_keys=("entry_z", "risk_budget_ratio"),
        grid_adjacency=True,
    )
    assert selected is not None
    # A diagonal relationship cannot manufacture a neighborhood; the middle
    # point is the only bridge between both axes and is therefore the stable center.
    assert selected["entry_z"] == 1.8
    assert selected["risk_budget_ratio"] == 0.002
