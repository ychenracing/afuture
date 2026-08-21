from pathlib import Path
import importlib.util
import pandas as pd

spec = importlib.util.spec_from_file_location("evidence", Path(__file__).with_name("evaluate_daily_relative_strategy.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cols = ["datetime","open","high","low","close","volume","hold","symbol","product"]
current = pd.DataFrame([
    ["2025-01-02 15:00:00",100.0,100.0,100.0,100.0,10.0,8000.0,"OI2501","OI"],
    ["2025-01-02 15:00:00",90.0,90.0,90.0,90.0,100.0,80000.0,"OI2505","OI"],
    ["2025-01-02 23:00:00",80.0,80.0,80.0,80.0,100.0,80000.0,"OI2505","OI"],
    ["2025-01-03 23:00:00",101.0,101.0,101.0,101.0,20.0,9000.0,"OI2501","OI"],
    ["2025-01-03 23:00:00",91.0,91.0,91.0,91.0,120.0,81000.0,"OI2505","OI"],
], columns=cols)
prior = current.iloc[0:0].copy()
frames = module.build_pair_frames(prior, current)
pair = frames[("OI","OI2501","OI2505")]
# 2025-01-02 近月没有生产采样窗口行情，因此整天必须缺席；不能退回 15:00
# 再与远月 23:00 拼成不可交易价差。下一天两腿都有 23:00 时才形成样本。
assert len(pair) == 1
assert str(pair.iloc[0].datetime.date()) == "2025-01-03"
assert float(pair.iloc[0].near) == 101.0
assert float(pair.iloc[0].far) == 91.0
assert float(pair.iloc[0].raw) == 10.0
