from pathlib import Path
import importlib.util
import sys
import pandas as pd

spec = importlib.util.spec_from_file_location(
    "structural", Path(__file__).with_name("evaluate_structural_rotation_specific.py")
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

# Frozen safety boundaries: target leverage is finite, one structure at a time, and
# the worst margin proxy still fits within the historical feasibility cap.
assert module.MAX_GROSS_LEVERAGE == 4.2
assert module.MAX_MARGIN_RATIO_PROXY == 0.85
assert max(module.MARGIN_PROXY.values()) * module.MAX_GROSS_LEVERAGE <= module.MAX_MARGIN_RATIO_PROXY
assert module.STRESS_COST_BPS == 2.0 * module.BASE_COST_BPS
assert set(module.QUALITY_WEIGHT) == {"steel", "coke", "soy", "bufu"}

# Point-in-time roll test: day-2 return must use the contract selected on day 1 even
# if day-2 OI has already switched to the next contract.
module.MIN_PRODUCT_DAYS = 3
rows = []
dates = [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-06")]
for product in module.REQUIRED_PRODUCTS:
    first = f"{product}2505"
    second = f"{product}2509"
    for index, day in enumerate(dates):
        first_close = 100.0 + index * 10.0
        second_close = 200.0 + index * 20.0
        first_oi = 1000.0 if index == 0 else 100.0
        second_oi = 100.0 if index == 0 else 2000.0
        rows.append({
            "date": day,
            "delivery": pd.Timestamp("2025-05-15"),
            "product": product,
            "symbol": first,
            "close": first_close,
            "volume": 1000.0,
            "hold": first_oi,
        })
        rows.append({
            "date": day,
            "delivery": pd.Timestamp("2025-09-15"),
            "product": product,
            "symbol": second,
            "close": second_close,
            "volume": 1000.0,
            "hold": second_oi,
        })
raw = pd.DataFrame(rows)
close, returns, selections, quality = module.build_roll_safe_panel(raw)
# 2025-01-03 realizes 110/100-1 from the contract selected on 2025-01-02; it must
# not use 220/200-1 because the day-2 OI switch is only a decision for day 2->3.
assert abs(float(returns.loc[pd.Timestamp("2025-01-03"), "A"]) - 0.10) < 1e-12
assert quality["A"]["rolls"] >= 1
assert int(selections["days_to_delivery"].min()) >= module.MIN_DAYS_TO_DELIVERY

# Fail closed: a path that loses 5% every next day must not receive any leverage.
index = pd.date_range("2024-08-21", "2026-02-20", freq="B")
path = pd.DataFrame(
    {
        "direction": [1] * len(index),
        "score": [3.0] * len(index),
        "next_return": [0.0] + [-0.05] * (len(index) - 1),
    },
    index=index,
)
paths = {name: path.copy() for name in module.QUALITY_WEIGHT}
assert module._choose_leverage(paths) == 0.0
