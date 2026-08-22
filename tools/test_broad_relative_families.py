from pathlib import Path
import importlib.util

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "broad_research",
    Path(__file__).with_name("evaluate_broad_relative_families.py"),
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# No OOS information is part of candidate definitions or selection score.
assert module.CANDIDATES
assert all(
    candidate.family in {"momentum", "slow_fast", "reversal", "negative_skew"}
    for candidate in module.CANDIDATES
)

# Synthetic cross-section with a persistent winner/loser proves the portfolio
# remains market-neutral and the signal is lagged by one observation.
dates = pd.date_range("2025-01-01", periods=80, freq="B")
returns = pd.DataFrame(
    {
        "A": [0.01] * 80,
        "B": [0.005] * 80,
        "C": [-0.005] * 80,
        "D": [-0.01] * 80,
    },
    index=dates,
)
candidate = module.Candidate("momentum", 20, 0, 5, 0.25)
signal = module.signal_for(returns, candidate)
series = module.simulate(
    returns, signal, rebalance=5, tail_fraction=0.25, cost_bps=0.0
)
assert series.iloc[:20].abs().sum() == 0.0
assert series.iloc[25:].mean() > 0.0

flat = module.metrics(pd.Series([0.0] * 30))
assert flat["annualized_return"] == 0.0
assert flat["max_drawdown"] == 0.0

# Leverage calibration must never choose a level whose calibration drawdown
# breaches the fixed 15% research cap.
rough = pd.Series([0.01] * 30 + [-0.08] * 3 + [0.01] * 30)
leverage = module.choose_leverage(rough)
assert leverage in module.LEVERAGE_GRID
assert module.metrics(rough * leverage)["max_drawdown"] > -0.15
