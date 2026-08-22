from pathlib import Path
import importlib.util
import sys

import pandas as pd

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location(
    "intraday_research", TOOLS / "evaluate_specific_pair_intraday.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

# Build >1000 exact common day-session bars. OI leadership flips on day 120;
# because contract choice is lagged, the new contract may only be used from day 121.
days = pd.date_range("2025-01-02", periods=260, freq="B")
rows = []
for product in ("BU", "FU", "PP", "V"):
    for day_index, day in enumerate(days):
        first_oi = 20000.0 if day_index <= 120 else 10000.0
        second_oi = 10000.0 if day_index <= 120 else 25000.0
        for hour in (10, 11, 14, 15):
            for suffix, delivery, close, hold in (
                ("2609", "2026-09-15", 100.0 + day_index * 0.05 + hour * 0.001, first_oi),
                ("2701", "2027-01-15", 200.0 + day_index * 0.10 + hour * 0.002, second_oi),
            ):
                rows.append(
                    {
                        "datetime": day + pd.Timedelta(hours=hour),
                        "product": product,
                        "symbol": f"{product}{suffix}",
                        "delivery": delivery,
                        "close": close,
                        "volume": 5000.0,
                        "hold": hold,
                    }
                )

close, returns, selections, quality = module.build_roll_safe_products(pd.DataFrame(rows))
assert len(close) >= 1000
assert list(close.columns) == ["BU", "FU", "PP", "V"]
assert all(item["selected_days"] == 259 for item in quality.values())

bu = selections[selections["product"] == "BU"].set_index("date")
# Day index 120 still uses OI from 119 (first contract), day 121 uses OI from 120,
# and day 122 is the first selection that sees the post-flip OI from day 121.
assert bu.loc[days[120], "symbol"] == "BU2609"
assert bu.loc[days[121], "symbol"] == "BU2609"
assert bu.loc[days[122], "symbol"] == "BU2701"

# Day-session-only execution: every retained timestamp must be inside the fixed window.
minutes = close.index.hour * 60 + close.index.minute
assert int(minutes.min()) >= module.START_MINUTE
assert int(minutes.max()) <= module.END_MINUTE

flat = module._metrics(pd.Series([0.0] * 100, index=pd.date_range("2025-01-01", periods=100, freq="h")))
assert flat["annualized_return"] == 0.0
assert flat["max_drawdown"] == 0.0
assert len(module.PROFILES) == 24
assert module.MAX_ACTIVE_PAIRS == 1
assert module.MAX_GROSS_LEVERAGE == 2.0
