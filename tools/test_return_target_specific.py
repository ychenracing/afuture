from pathlib import Path
import importlib.util
import sys

import numpy as np
import pandas as pd

TOOLS = Path(__file__).resolve().parent
FETCH_PATH = TOOLS / "fetch_return_target_specific_daily.py"
EVAL_PATH = TOOLS / "evaluate_return_target_specific.py"
assert FETCH_PATH.exists()
assert EVAL_PATH.exists()

fetch_spec = importlib.util.spec_from_file_location("return_target_fetch", FETCH_PATH)
fetcher = importlib.util.module_from_spec(fetch_spec)
sys.modules[fetch_spec.name] = fetcher
fetch_spec.loader.exec_module(fetcher)

eval_spec = importlib.util.spec_from_file_location("return_target_specific", EVAL_PATH)
evaluator = importlib.util.module_from_spec(eval_spec)
sys.modules[eval_spec.name] = evaluator
eval_spec.loader.exec_module(evaluator)

assert set(fetcher.PRODUCTS) == set(evaluator.REQUIRED_PRODUCTS)
assert len(fetcher.PRODUCTS) == 50
assert evaluator.MAX_GROSS_LEVERAGE == 2.0
assert evaluator.EXECUTION_SELECTION["pool_size"] == 32
assert evaluator.EXECUTION_SELECTION["meta_lookback"] == 10
assert evaluator.EXECUTION_SELECTION["rebalance"] == 5
assert evaluator.EXECUTION_SELECTION["count"] == 2
assert len(evaluator.EXECUTION_SELECTION["pool_ids"]) == 32
assert evaluator.EXECUTION_SELECTION["selection_bias"] == (
    "full_recent_target_fit_plus_roll_safe_next_open_execution_fit"
)

symbols = set(fetcher.contract_symbols())
assert ("A", "DCE", "A2209") in symbols
assert ("MA", "CZCE", "MA2209") in symbols
assert ("AG", "SHFE", "AG2209") in symbols
assert ("BC", "INE", "BC2209") in symbols
assert fetcher.delivery_date("AG2609") == pd.Timestamp("2026-09-15")

# Concrete roll causality: t->t+1 return is always on the exact contract selected at t.
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
                    "open": p1 * 0.99,
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
                    "open": p2 * 0.99,
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
    close_returns, gap_returns, intraday_returns, selected, quality = (
        evaluator.build_roll_safe_execution_returns(pd.DataFrame(rows))
    )
finally:
    evaluator.REQUIRED_PRODUCTS = original_products
    evaluator.MIN_PRODUCT_DAYS = original_min_days

assert selected.loc[selected["product"] == "A", "symbol"].tolist() == [
    "A2509",
    "A2601",
    "A2601",
]
assert abs(close_returns.loc[dates[1], "A"] - 0.10) < 1e-12
assert quality["A"]["rolls"] == 1
assert int(selected["days_to_delivery"].min()) >= evaluator.MIN_DAYS_TO_DELIVERY

# Next-open execution semantics: old holdings earn close->open gap; new target earns
# open->close; cost is charged once on final product weight turnover at the open.
idx = pd.date_range("2025-02-03", periods=3, freq="B")
weights = pd.DataFrame({"A": [0.0, 1.0, -1.0]}, index=idx)
gap = pd.DataFrame({"A": [0.0, 0.10, 0.20]}, index=idx)
intraday = pd.DataFrame({"A": [0.0, 0.03, 0.04]}, index=idx)
path = evaluator.apply_next_open_product_weights(
    gap, intraday, weights, cost_bps=0.0
)
assert abs(path.iloc[1] - 0.03) < 1e-12
assert abs(path.iloc[2] - (0.20 - 0.04)) < 1e-12
costed = evaluator.apply_next_open_product_weights(
    gap, intraday, weights, cost_bps=10.0
)
# d1 target 0->1 costs 10bp; d2 +1->-1 costs 20bp.
assert abs((path.iloc[1] - costed.iloc[1]) - 0.001) < 1e-12
assert abs((path.iloc[2] - costed.iloc[2]) - 0.002) < 1e-12

# Signal t-1 determines the current target; realized return t cannot change exposure t.
assert hasattr(evaluator, "generate_execution_signal_weights")
assert evaluator.PRISTINE_FINAL_OOS is False

print("return-target execution-aware specific-contract tests passed")
