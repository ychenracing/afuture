from pathlib import Path
import importlib.util
import pandas as pd

spec = importlib.util.spec_from_file_location(
    "evidence", Path(__file__).with_name("evaluate_daily_relative_strategy.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cols = [
    "datetime", "open", "high", "low", "close", "volume", "hold", "symbol", "product"
]
current = pd.DataFrame(
    [
        # 1/2 白天属于 1/2 交易日。
        ["2025-01-02 15:00:00", 100, 100, 100, 100, 100, 8000, "OI2501", "OI"],
        ["2025-01-02 15:00:00", 90, 90, 90, 90, 200, 80000, "OI2505", "OI"],
        # 1/2 夜盘属于 1/3 交易日，但近月缺 23:00，因此 1/3 不得形成 pair sample。
        ["2025-01-02 21:00:00", 101, 101, 101, 101, 10, 8100, "OI2501", "OI"],
        ["2025-01-02 21:00:00", 91, 91, 91, 91, 20, 80100, "OI2505", "OI"],
        ["2025-01-02 23:00:00", 92, 92, 92, 92, 30, 80200, "OI2505", "OI"],
        # 1/3 日盘证明 1/2 夜盘的下一真实交易日确实是 1/3。
        ["2025-01-03 09:00:00", 102, 102, 102, 102, 300, 8200, "OI2501", "OI"],
        ["2025-01-03 09:00:00", 92, 92, 92, 92, 400, 80300, "OI2505", "OI"],
        # 周五 1/3 夜盘按中国期货语义属于下一个日盘交易日 1/6。
        ["2025-01-03 21:00:00", 103, 103, 103, 103, 11, 8300, "OI2501", "OI"],
        ["2025-01-03 21:00:00", 93, 93, 93, 93, 21, 80400, "OI2505", "OI"],
        ["2025-01-03 23:00:00", 104, 104, 104, 104, 12, 8400, "OI2501", "OI"],
        ["2025-01-03 23:00:00", 94, 94, 94, 94, 22, 80500, "OI2505", "OI"],
        ["2025-01-06 09:00:00", 105, 105, 105, 105, 500, 8500, "OI2501", "OI"],
        ["2025-01-06 09:00:00", 95, 95, 95, 95, 600, 80600, "OI2505", "OI"],
    ],
    columns=cols,
)
prior = current.iloc[0:0].copy()
frames = module.build_pair_frames(prior, current)
pair = frames[("OI", "OI2501", "OI2505")]

assert len(pair) == 1
assert str(pair.iloc[0].datetime.date()) == "2025-01-06"
assert str(pair.iloc[0].sample_timestamp) == "2025-01-03 23:00:00"
assert float(pair.iloc[0].near) == 104.0
assert float(pair.iloc[0].far) == 94.0
assert float(pair.iloc[0].raw) == 10.0
assert float(pair.iloc[0].near_vol) == 23.0
assert float(pair.iloc[0].far_vol) == 43.0
assert float(pair.iloc[0].near_hold) == 8400.0
assert float(pair.iloc[0].far_hold) == 80500.0

# Production Auto only keeps contracts at least 20 days from delivery/expiry and then
# takes the front three. On trading day 2025-01-03, M2501 is inside the 20-day
# blackout proxy, so the research universe must start from M2505 and may form only
# M2505-M2509 and M2509-M2601. A far fourth pair must not appear either.
front_rows = []
for symbol, price in (("M2501", 2800), ("M2505", 2810), ("M2509", 2820), ("M2601", 2830)):
    front_rows.append(["2025-01-02 23:00:00", price, price, price, price, 10, 20000, symbol, "M"])
    front_rows.append(["2025-01-03 09:00:00", price, price, price, price, 100, 21000, symbol, "M"])
front = pd.DataFrame(front_rows, columns=cols)
front_frames = module.build_pair_frames(front.iloc[0:0].copy(), front)
assert ("M", "M2501", "M2505") not in front_frames
assert ("M", "M2505", "M2509") in front_frames
assert ("M", "M2509", "M2601") in front_frames
assert len([key for key in front_frames if key[0] == "M"]) == 2
