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
        # 下一实际日盘是周一 1/6；这些未来 bars 只用于确定 trading-day 映射，
        # 绝不能被计入 1/3 23:00 当时可见的 volume。
        ["2025-01-06 09:00:00", 105, 105, 105, 105, 500, 8500, "OI2501", "OI"],
        ["2025-01-06 09:00:00", 95, 95, 95, 95, 600, 80600, "OI2505", "OI"],
    ],
    columns=cols,
)
prior = current.iloc[0:0].copy()
frames = module.build_pair_frames(prior, current)
pair = frames[("OI", "OI2501", "OI2505")]

# 1/2 夜盘缺近月 23:00，所以 1/3 交易日没有样本；1/3 周五夜盘两腿都有
# 23:00，形成的是 1/6 交易日样本，而不是自然日 1/3 样本。
assert len(pair) == 1
assert str(pair.iloc[0].datetime.date()) == "2025-01-06"
assert str(pair.iloc[0].sample_timestamp) == "2025-01-03 23:00:00"
assert float(pair.iloc[0].near) == 104.0
assert float(pair.iloc[0].far) == 94.0
assert float(pair.iloc[0].raw) == 10.0

# 23:00 可见成交量只包含该交易日已经发生的 21:00 + 23:00 夜盘 bars。
assert float(pair.iloc[0].near_vol) == 23.0
assert float(pair.iloc[0].far_vol) == 43.0
assert float(pair.iloc[0].near_hold) == 8400.0
assert float(pair.iloc[0].far_hold) == 80500.0
