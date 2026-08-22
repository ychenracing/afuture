from pathlib import Path
import importlib.util
import sys

import numpy as np
import pandas as pd

TOOLS = Path(__file__).resolve().parent
FETCH_PATH = TOOLS / "fetch_return_target_specific_daily.py"
EVAL_PATH = TOOLS / "evaluate_return_target_specific.py"
assert FETCH_PATH.exists(), "specific-contract return-target fetcher is not implemented yet"
assert EVAL_PATH.exists(), "specific-contract return-target evaluator is not implemented yet"

fetch_spec = importlib.util.spec_from_file_location("return_target_fetch", FETCH_PATH)
fetcher = importlib.util.module_from_spec(fetch_spec)
sys.modules[fetch_spec.name] = fetcher
fetch_spec.loader.exec_module(fetcher)

eval_spec = importlib.util.spec_from_file_location("return_target_specific", EVAL_PATH)
evaluator = importlib.util.module_from_spec(eval_spec)
sys.modules[eval_spec.name] = evaluator
eval_spec.loader.exec_module(evaluator)

# The L4 universe covers the exact 50-product L3 broad universe and never raises
# the leverage cap beyond the already-selected 2x target fit.
assert set(fetcher.PRODUCTS) == set(evaluator.REQUIRED_PRODUCTS)
assert len(fetcher.PRODUCTS) == 50
assert evaluator.MAX_GROSS_LEVERAGE == 2.0
assert evaluator.FROZEN_SELECTION["meta_lookback"] == 10
assert evaluator.FROZEN_SELECTION["rebalance"] == 5
assert evaluator.FROZEN_SELECTION["count"] == 2
assert evaluator.FROZEN_SELECTION["pool_size"] == 24
assert len(evaluator.FROZEN_SELECTION["pool_ids"]) == 24

# Concrete-symbol generation is deterministic and spans all four exchanges.
symbols = set(fetcher.contract_symbols())
assert ("A", "DCE", "A2209") in symbols
assert ("MA", "CZCE", "MA2209") in symbols
assert ("AG", "SHFE", "AG2209") in symbols
assert ("BC", "INE", "BC2209") in symbols
assert fetcher.delivery_date("AG2609") == pd.Timestamp("2026-09-15")

# A roll test with a giant cross-contract price gap proves the return at t+1 comes
# from the exact contract selected at t, not from joining the next dominant price.
dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
rows = []
for product in ("A", "M"):
    first = (100.0, 110.0, 108.0) if product == "A" else (50.0, 55.0, 54.0)
    second = (1000.0, 1100.0, 1210.0) if product == "A" else (500.0, 550.0, 605.0)
    for day, p1, p2, oi1, oi2 in zip(
        dates,
        first,
        second,
        (100.0, 80.0, 70.0),
        (50.0, 120.0, 140.0),
    ):
        rows.extend(
            [
                {
                    "date": day,
                    "product": product,
                    "exchange": "DCE",
                    "symbol": f"{product}2509",
                    "delivery": "2025-09-15",
                    "close": p1,
                    "volume": 1000.0,
                    "hold": oi1,
                },
                {
                    "date": day,
                    "product": product,
                    "exchange": "DCE",
                    "symbol": f"{product}2601",
                    "delivery": "2026-01-15",
                    "close": p2,
                    "volume": 900.0,
                    "hold": oi2,
                },
            ]
        )

original_products = evaluator.REQUIRED_PRODUCTS
original_min_days = evaluator.MIN_PRODUCT_DAYS
try:
    evaluator.REQUIRED_PRODUCTS = ("A", "M")
    evaluator.MIN_PRODUCT_DAYS = 3
    returns, selected, quality = evaluator.build_roll_safe_returns(pd.DataFrame(rows))
finally:
    evaluator.REQUIRED_PRODUCTS = original_products
    evaluator.MIN_PRODUCT_DAYS = original_min_days

assert selected.loc[selected["product"] == "A", "symbol"].tolist() == [
    "A2509",
    "A2601",
    "A2601",
]
assert abs(returns.loc[dates[1], "A"] - 0.10) < 1e-12
assert abs(returns.loc[dates[2], "A"] - 0.10) < 1e-12
assert quality["A"]["rolls"] == 1
assert quality["A"]["missing_next_ratio"] < 0.01
assert int(selected["days_to_delivery"].min()) >= evaluator.MIN_DAYS_TO_DELIVERY

# Frozen continuous-series weights applied to roll-safe returns must use date-aligned
# weights only and preserve the global gross cap.
weights = pd.DataFrame(
    [
        {"date": dates[1], "product": "A", "weight": 1.0},
        {"date": dates[1], "product": "M", "weight": -1.0},
        {"date": dates[2], "product": "A", "weight": 1.0},
        {"date": dates[2], "product": "M", "weight": -1.0},
    ]
)
replay, gross = evaluator.replay_frozen_weights(returns, weights, cost_bps=0.0)
assert gross.max() <= 2.0 + 1e-12
assert abs(replay.loc[dates[1]]) < 1e-12  # both legs move +10%
assert abs(replay.loc[dates[2]]) < 1e-12

# L4 config is frozen. It may recompute signals on roll-safe data but cannot reselect
# a new pool from L4 returns to make the target easier.
assert evaluator.FROZEN_SELECTION["selection_bias"] == "full_recent_target_fit"
assert evaluator.PRISTINE_FINAL_OOS is False

print("return-target specific-contract causal tests passed")
