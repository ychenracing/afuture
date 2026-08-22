import pandas as pd
from afuture.directional_acceptance import DirectionalProductionAcceptance, ProductionMechanicsConfig


def test_simulation_orders_gap_rebalance_intraday_and_cost():
    raw = pd.DataFrame([
        {"date":"2026-08-20","product":"A","exchange":"DCE","symbol":"A2609","delivery":"2026-12-15","open":99,"close":99,"volume":5000,"hold":30000},
        {"date":"2026-08-21","product":"A","exchange":"DCE","symbol":"A2609","delivery":"2026-12-15","open":100,"close":110,"volume":5000,"hold":30000},
        {"date":"2026-08-24","product":"A","exchange":"DCE","symbol":"A2609","delivery":"2026-12-15","open":111,"close":112,"volume":5000,"hold":30000},
    ])
    weights = pd.DataFrame({"A":[1.0,0.0]}, index=pd.to_datetime(["2026-08-21","2026-08-24"]))
    sim = DirectionalProductionAcceptance(ProductionMechanicsConfig(initial_capital=100000,max_contract_volume=100,max_daily_loss_ratio=.5,max_total_drawdown_ratio=.8,max_margin_ratio=.9,min_available_ratio=0))
    result = sim.simulate(raw, weights, cost_bps=5)
    assert abs(result.daily.loc[pd.Timestamp("2026-08-21"),"equity"] - 109950) < 1e-9
    assert abs(result.final_equity - 110894.5) < 1e-9


def test_risk_breach_halts_future_reopening():
    raw = pd.DataFrame([
        {"date":"2026-08-20","product":"A","exchange":"DCE","symbol":"A2609","delivery":"2026-12-15","open":100,"close":100,"volume":5000,"hold":30000},
        {"date":"2026-08-21","product":"A","exchange":"DCE","symbol":"A2609","delivery":"2026-12-15","open":100,"close":90,"volume":5000,"hold":30000},
        {"date":"2026-08-24","product":"A","exchange":"DCE","symbol":"A2609","delivery":"2026-12-15","open":90,"close":120,"volume":5000,"hold":30000},
    ])
    weights = pd.DataFrame({"A":[1.0,1.0]}, index=pd.to_datetime(["2026-08-21","2026-08-24"]))
    sim = DirectionalProductionAcceptance(ProductionMechanicsConfig(initial_capital=100000,max_daily_loss_ratio=.05,max_total_drawdown_ratio=.3,max_margin_ratio=.9,min_available_ratio=0))
    result = sim.simulate(raw, weights, cost_bps=0)
    assert result.daily.loc[pd.Timestamp("2026-08-21"),"risk_reason"] == "daily loss limit reached"
    assert result.daily.loc[pd.Timestamp("2026-08-24"),"gross_notional"] == 0
    assert result.first_divergence == "daily loss limit reached"
