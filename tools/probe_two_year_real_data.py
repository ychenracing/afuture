"""Fetch real specific-contract futures history through AKShare for two-year research.

The exchange aggregate endpoint can reject cloud-runner requests, so this script uses
AKShare's documented Sina specific-contract history interfaces. It is research-only and
never enters the production trading path.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re

import akshare as ak
import pandas as pd

START_DATE = pd.Timestamp("2024-08-21")
END_DATE = pd.Timestamp("2026-08-20 23:59:59")


def symbols() -> list[str]:
    result: list[str] = []
    for product in ("M", "C", "P"):
        for contract in ("2501", "2505", "2509", "2601", "2605", "2609", "2701"):
            result.append(f"{product}{contract}")
    for contract in ("2410", "2501", "2505", "2510", "2601", "2605", "2610", "2701"):
        result.append(f"RB{contract}")
    for contract in ("2409", "2501", "2505", "2509", "2601", "2605", "2609", "2701"):
        result.append(f"TA{contract}")
    return result


def product_of(symbol: str) -> str:
    match = re.match(r"[A-Za-z]+", symbol)
    return match.group(0).upper() if match else ""


def fetch_daily(symbol: str) -> pd.DataFrame:
    frame = ak.futures_zh_daily_sina(symbol=symbol).copy()
    if frame.empty:
        return frame
    date_col = "date" if "date" in frame.columns else str(frame.columns[0])
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame = frame.loc[
        (frame[date_col] >= START_DATE.normalize())
        & (frame[date_col] <= END_DATE.normalize())
    ].copy()
    if not frame.empty:
        frame.rename(columns={date_col: "date"}, inplace=True)
        frame["symbol"] = symbol
        frame["product"] = product_of(symbol)
    return frame


def fetch_60m(symbol: str) -> pd.DataFrame:
    frame = ak.futures_zh_minute_sina(symbol=symbol, period="60").copy()
    if frame.empty:
        return frame
    time_col = "datetime" if "datetime" in frame.columns else str(frame.columns[0])
    frame[time_col] = pd.to_datetime(frame[time_col], errors="coerce")
    frame = frame.loc[
        (frame[time_col] >= START_DATE) & (frame[time_col] <= END_DATE)
    ].copy()
    if not frame.empty:
        frame.rename(columns={time_col: "datetime"}, inplace=True)
        frame["symbol"] = symbol
        frame["product"] = product_of(symbol)
    return frame


def minute_depth_probe() -> dict[str, object]:
    """Document why 60m is the highest frequency with rolling two-year coverage."""
    result: dict[str, object] = {}
    for symbol in ("M2501", "M2605", "RB2505", "RB2605", "TA2505", "TA2605"):
        periods: dict[str, object] = {}
        for period in ("60", "15", "1"):
            try:
                frame = ak.futures_zh_minute_sina(symbol=symbol, period=period)
            except Exception as exc:
                periods[period] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            if frame is None or frame.empty:
                periods[period] = {"rows": 0}
                continue
            time_col = "datetime" if "datetime" in frame.columns else str(frame.columns[0])
            timestamps = pd.to_datetime(frame[time_col], errors="coerce").dropna()
            periods[period] = {
                "rows": int(len(frame)),
                "first": timestamps.min().isoformat() if not timestamps.empty else None,
                "last": timestamps.max().isoformat() if not timestamps.empty else None,
                "days": int(timestamps.dt.date.nunique()) if not timestamps.empty else 0,
            }
        result[symbol] = periods
    return result


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    daily_rows: list[pd.DataFrame] = []
    hourly_rows: list[pd.DataFrame] = []
    status: dict[str, object] = {}

    for symbol in symbols():
        row: dict[str, object] = {}
        try:
            daily = fetch_daily(symbol)
            if not daily.empty:
                daily_rows.append(daily)
                row["daily_rows"] = int(len(daily))
                row["daily_first"] = daily["date"].min().date().isoformat()
                row["daily_last"] = daily["date"].max().date().isoformat()
        except Exception as exc:
            row["daily_error"] = f"{type(exc).__name__}: {exc}"

        try:
            hourly = fetch_60m(symbol)
            if not hourly.empty:
                hourly_rows.append(hourly)
                row["hourly_rows"] = int(len(hourly))
                row["hourly_first"] = hourly["datetime"].min().isoformat()
                row["hourly_last"] = hourly["datetime"].max().isoformat()
                row["hourly_days"] = int(hourly["datetime"].dt.date.nunique())
        except Exception as exc:
            row["hourly_error"] = f"{type(exc).__name__}: {exc}"
        status[symbol] = row

    daily_all = pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()
    hourly_all = pd.concat(hourly_rows, ignore_index=True) if hourly_rows else pd.DataFrame()
    if daily_all.empty or hourly_all.empty:
        raise RuntimeError("specific-contract history did not provide both daily and 60m data")

    products: dict[str, object] = {}
    for product, daily_group in daily_all.groupby("product"):
        hourly_group = hourly_all.loc[hourly_all["product"] == product]
        products[str(product)] = {
            "daily_trading_days": int(daily_group["date"].dt.date.nunique()),
            "daily_first": daily_group["date"].min().date().isoformat(),
            "daily_last": daily_group["date"].max().date().isoformat(),
            "hourly_rows": int(len(hourly_group)),
            "hourly_trading_days": int(hourly_group["datetime"].dt.date.nunique()),
            "hourly_first": hourly_group["datetime"].min().isoformat() if not hourly_group.empty else None,
            "hourly_last": hourly_group["datetime"].max().isoformat() if not hourly_group.empty else None,
            "symbols": sorted(daily_group["symbol"].astype(str).unique().tolist()),
        }

    report = {
        "source": "AKShare Sina specific futures contracts",
        "start_date": START_DATE.date().isoformat(),
        "end_date": END_DATE.date().isoformat(),
        "generated_on": date.today().isoformat(),
        "products": products,
        "symbol_status": status,
        "minute_depth_probe": minute_depth_probe(),
    }
    daily_all.to_csv(output / "two_year_specific_contract_daily.csv", index=False)
    hourly_all.to_csv(output / "two_year_specific_contract_60m.csv", index=False)
    (output / "realdata_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
