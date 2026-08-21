"""Fetch an older OI 60-minute history for independent robustness checks.

Recent 2024-2026 data remains the required primary backtest. This older window is only
used to reject rules that appear to repair a known recent failure by hindsight.
"""

from __future__ import annotations

from pathlib import Path

import akshare as ak
import pandas as pd

START = pd.Timestamp("2022-08-21")
END = pd.Timestamp("2024-08-20 23:59:59")
SYMBOLS = [
    "OI2301", "OI2305", "OI2309",
    "OI2401", "OI2405", "OI2409",
    "OI2501",
]


def main() -> None:
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        frame = ak.futures_zh_minute_sina(symbol=symbol, period="60").copy()
        if frame.empty:
            continue
        column = "datetime" if "datetime" in frame.columns else str(frame.columns[0])
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
        frame = frame.loc[(frame[column] >= START) & (frame[column] <= END)].copy()
        if frame.empty:
            continue
        frame.rename(columns={column: "datetime"}, inplace=True)
        frame["symbol"] = symbol
        frame["product"] = "OI"
        frames.append(frame)

    if not frames:
        raise RuntimeError("older OI history is empty")
    data = pd.concat(frames, ignore_index=True).sort_values(["datetime", "symbol"])
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    data.to_csv(output / "oi_2022_2024_60m.csv", index=False)
    print(
        f"rows={len(data)} symbols={data['symbol'].nunique()} "
        f"days={data['datetime'].dt.date.nunique()} "
        f"first={data['datetime'].min()} last={data['datetime'].max()}"
    )


if __name__ == "__main__":
    main()
