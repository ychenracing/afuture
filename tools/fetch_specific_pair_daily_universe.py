"""Fetch targeted specific-contract daily history for the L4 economic-pair check.

Only roots that survived the broad L3 rotation screen are downloaded. Sina returns the
full history for one concrete contract per request, which is materially cheaper than
requesting every exchange trading day across all contracts.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import time

import akshare as ak
import pandas as pd

START = pd.Timestamp("2022-05-01")
END = pd.Timestamp("2026-08-20")

DCE_PRODUCTS = ("P", "Y", "PP", "V", "J", "JM")
SHFE_PRODUCTS = ("AL", "ZN", "BU", "FU", "CU")
KEY_MONTHS = (1, 5, 9)


def _month_range(start: pd.Timestamp, end: pd.Timestamp):
    current = pd.Timestamp(start.year, start.month, 1)
    final = pd.Timestamp(end.year, end.month, 1)
    while current <= final:
        yield current.year, current.month
        current += pd.offsets.MonthBegin(1)


def contract_symbols() -> list[tuple[str, str, str]]:
    """Return (product, exchange, concrete symbol) without future-data selection."""
    rows: list[tuple[str, str, str]] = []
    for product in DCE_PRODUCTS:
        for year in range(START.year, END.year + 2):
            for month in KEY_MONTHS:
                delivery = pd.Timestamp(year, month, 1)
                if delivery < START - pd.DateOffset(months=4):
                    continue
                if delivery > END + pd.DateOffset(months=6):
                    continue
                rows.append((product, "DCE", f"{product}{year % 100:02d}{month:02d}"))
    for product in SHFE_PRODUCTS:
        for year, month in _month_range(
            START - pd.DateOffset(months=3),
            END + pd.DateOffset(months=5),
        ):
            rows.append((product, "SHFE", f"{product}{year % 100:02d}{month:02d}"))
    return rows


def delivery_date(symbol: str) -> pd.Timestamp:
    digits = "".join(character for character in symbol if character.isdigit())[-4:]
    if len(digits) != 4:
        raise ValueError(f"cannot infer delivery month from {symbol}")
    year = 2000 + int(digits[:2])
    month = int(digits[2:])
    return pd.Timestamp(date(year, month, 15))


def _download(symbol: str) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            frame = ak.futures_zh_daily_sina(symbol=symbol).copy()
            if not frame.empty:
                return frame
        except Exception as exc:  # network/provider failures are recorded below
            last_error = exc
        if attempt < 2:
            time.sleep(0.25 * (2**attempt))
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    for product, exchange, symbol in contract_symbols():
        try:
            frame = _download(symbol)
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
            continue
        if frame.empty:
            failures.append(f"{symbol}: empty")
            continue
        if "date" not in frame.columns or "close" not in frame.columns:
            failures.append(f"{symbol}: missing date/close")
            continue
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume", "hold", "settle"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.loc[
            (frame["date"] >= START) & (frame["date"] <= END)
        ].copy()
        frame.dropna(subset=["date", "close"], inplace=True)
        frame = frame[frame["close"] > 0]
        if frame.empty:
            continue
        frame["symbol"] = symbol
        frame["product"] = product
        frame["exchange"] = exchange
        frame["delivery"] = delivery_date(symbol)
        frames.append(frame)
        time.sleep(0.03)

    if not frames:
        raise RuntimeError("specific-contract daily fetch returned no usable rows")

    data = pd.concat(frames, ignore_index=True)
    data.drop_duplicates(["product", "symbol", "date"], keep="last", inplace=True)
    data.sort_values(["date", "product", "symbol"], inplace=True)
    data.to_csv(output / "specific_pair_daily_contracts.csv", index=False)

    summary = (
        data.groupby("product")
        .agg(
            rows=("date", "size"),
            contracts=("symbol", "nunique"),
            trading_days=("date", "nunique"),
            first=("date", "min"),
            last=("date", "max"),
        )
        .reset_index()
        .sort_values("product")
    )
    summary.to_csv(output / "specific_pair_daily_summary.csv", index=False)
    (output / "specific_pair_daily_failures.txt").write_text(
        "\n".join(failures), encoding="utf-8"
    )
    print(summary.to_string(index=False))
    print(
        f"\nrows={len(data)} contracts={data['symbol'].nunique()} "
        f"products={data['product'].nunique()} failures={len(failures)}"
    )


if __name__ == "__main__":
    main()
