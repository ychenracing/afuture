"""Fetch the broad real 30-minute contract universe for higher-resolution research."""

from __future__ import annotations

from pathlib import Path

import akshare as ak
import pandas as pd

from fetch_two_year_60m_universe import END, START, product_of, symbols


def main() -> None:
    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for symbol in symbols():
        try:
            frame = ak.futures_zh_minute_sina(symbol=symbol, period="30").copy()
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
            continue
        if frame.empty:
            failures.append(f"{symbol}: empty")
            continue
        column = "datetime" if "datetime" in frame.columns else str(frame.columns[0])
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
        frame = frame.loc[(frame[column] >= START) & (frame[column] <= END)].copy()
        if frame.empty:
            continue
        frame.rename(columns={column: "datetime"}, inplace=True)
        frame["symbol"] = symbol
        frame["product"] = product_of(symbol)
        frames.append(frame)

    if not frames:
        raise RuntimeError("30m research universe returned no history")
    data = pd.concat(frames, ignore_index=True).sort_values(["datetime", "product", "symbol"])
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    data.to_csv(output / "two_year_broad_30m.csv", index=False)

    summary = []
    for product, group in data.groupby("product"):
        summary.append(
            {
                "product": product,
                "rows": int(len(group)),
                "symbols": int(group["symbol"].nunique()),
                "days": int(group["datetime"].dt.date.nunique()),
                "first": group["datetime"].min().isoformat(),
                "last": group["datetime"].max().isoformat(),
            }
        )
    summary_frame = pd.DataFrame(summary).sort_values("product")
    summary_frame.to_csv(output / "two_year_broad_30m_summary.csv", index=False)
    (output / "two_year_broad_30m_failures.txt").write_text("\n".join(failures), encoding="utf-8")
    print(summary_frame.to_string(index=False))
    if failures:
        print("\nUnavailable symbols:")
        print("\n".join(failures))


if __name__ == "__main__":
    main()
