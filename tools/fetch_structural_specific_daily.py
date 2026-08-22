"""Fetch concrete daily contracts needed by the structural-rotation L4 gate."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
import time

import akshare as ak
import pandas as pd

START = pd.Timestamp("2022-05-01")
END = pd.Timestamp("2026-08-20")
DCE_PRODUCTS = ("A", "I", "J", "JM", "M", "Y")
DCE_MONTHS = (1, 5, 9)
SHFE_MONTHS = {
    "RB": (1, 5, 10),
    "BU": tuple(range(1, 13)),
    "FU": tuple(range(1, 13)),
}
MAX_WORKERS = 8


def delivery_date(symbol: str) -> pd.Timestamp:
    digits = "".join(character for character in symbol if character.isdigit())[-4:]
    if len(digits) != 4:
        raise ValueError(f"cannot infer delivery month from {symbol}")
    return pd.Timestamp(date(2000 + int(digits[:2]), int(digits[2:]), 15))


def contract_symbols() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for product in DCE_PRODUCTS:
        for year in range(START.year, END.year + 2):
            for month in DCE_MONTHS:
                delivery = pd.Timestamp(year, month, 1)
                if START - pd.DateOffset(months=4) <= delivery <= END + pd.DateOffset(months=6):
                    rows.append((product, "DCE", f"{product}{year % 100:02d}{month:02d}"))
    for product, months in SHFE_MONTHS.items():
        for year in range(START.year, END.year + 2):
            for month in months:
                delivery = pd.Timestamp(year, month, 1)
                if START - pd.DateOffset(months=3) <= delivery <= END + pd.DateOffset(months=5):
                    rows.append((product, "SHFE", f"{product}{year % 100:02d}{month:02d}"))
    return rows


def _download(task: tuple[str, str, str]):
    product, exchange, symbol = task
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            frame = ak.futures_zh_daily_sina(symbol=symbol).copy()
            return task, frame, None
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (2 ** attempt))
    return task, pd.DataFrame(), last_error


def _clean(task, frame: pd.DataFrame):
    product, exchange, symbol = task
    if frame.empty:
        return None
    if "date" not in frame.columns or "close" not in frame.columns:
        raise ValueError("missing date/close")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "hold", "settle"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[(frame["date"] >= START) & (frame["date"] <= END)].copy()
    frame.dropna(subset=["date", "close"], inplace=True)
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return None
    frame["symbol"] = symbol
    frame["product"] = product
    frame["exchange"] = exchange
    frame["delivery"] = delivery_date(symbol)
    return frame


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    tasks = contract_symbols()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_download, task): task for task in tasks}
        for future in as_completed(futures):
            task, frame, error = future.result()
            product, exchange, symbol = task
            if error is not None:
                failures.append(f"{symbol}: {type(error).__name__}: {error}")
                continue
            try:
                cleaned = _clean(task, frame)
            except Exception as exc:
                failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
                continue
            if cleaned is None:
                failures.append(f"{symbol}: empty")
                continue
            frames.append(cleaned)

    if not frames:
        raise RuntimeError("structural specific-contract fetch returned no usable rows")
    data = pd.concat(frames, ignore_index=True)
    data.drop_duplicates(["product", "symbol", "date"], keep="last", inplace=True)
    data.sort_values(["date", "product", "symbol"], inplace=True)
    data.to_csv(output / "structural_specific_daily_contracts.csv", index=False)
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
    summary.to_csv(output / "structural_specific_daily_summary.csv", index=False)
    (output / "structural_specific_daily_failures.txt").write_text("\n".join(sorted(failures)), encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"rows={len(data)} contracts={data['symbol'].nunique()} products={data['product'].nunique()} failures={len(failures)}")


if __name__ == "__main__":
    main()
