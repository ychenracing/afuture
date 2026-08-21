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
], columns=cols)
prior = current.iloc[0:0].copy()
frames = module.build_pair_frames(prior, current)
pair = frames[("OI","OI2501","OI2505")]
assert float(pair.iloc[0].near) == 100.0
assert float(pair.iloc[0].far) == 90.0
assert float(pair.iloc[0].raw) == 10.0
