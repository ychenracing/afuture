"""Probe two years of real specific-contract futures daily data through AKShare.

The exchange aggregate endpoint can reject cloud-runner requests, so this probe uses
AKShare's documented Sina specific-contract history interface. It is research-only and
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
END_DATE = pd.Timestamp("2026-08-20")


def _symbols() -> list[str]:
    symbols: list[str] = []
    for product in ("M", "C", "P"):
        for contract in ("2501", "2505", "2509", "2601", "2605", "2609", "2701"):
            symbols.append(f"{product}{contract}")
    for contract in ("2410", "2501", "2505", "2510", "2601", "2605", "2610", "2701"):
        symbols.append(f"RB{contract}")
    # Sina/CZCE symbol conventions have changed over time. Probe both 4-digit and
    # exchange-style 3-digit forms and keep whichever endpoint actually returns data.
    for contract in ("2409", "2501", "2505", "2509", "2601", "2605", "2609", "2701"):
        symbols.append(f"TA{contract}")
        symbols.append(f"TA{contract[1:]}")
    return symbols


def _product(symbol: str) -> str:
    match = re.match(r"[A-Za-z]+", symbol)
    return match.group(0).upper() if match else ""


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    rows: list[pd.DataFrame] = []
    status: dict[str, object] = {}

    for symbol in _symbols():
        try:
            frame = ak.futures_zh_daily_sina(symbol=symbol)
        except Exception as exc:
            status[symbol] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if frame is None or frame.empty:
            status[symbol] = {"rows": 0}
            continue

        date_col = "date" if "date" in frame.columns else str(frame.columns[0])
        frame = frame.copy()
        frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
        frame = frame.loc[
            (frame[date_col] >= START_DATE) & (frame[date_col] <= END_DATE)
        ].copy()
        if frame.empty:
            status[symbol] = {"rows": 0, "outside_window": True}
            continue
        frame["symbol"] = symbol
        frame["product"] = _product(symbol)
        rows.append(frame)
        status[symbol] = {
            "rows": int(len(frame)),
            "first_date": frame[date_col].min().date().isoformat(),
            "last_date": frame[date_col].max().date().isoformat(),
        }

    combined = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if combined.empty:
        raise RuntimeError("Sina specific-contract endpoint returned no target history")

    product_summary: dict[str, object] = {}
    for product, group in combined.groupby("product"):
        dates = pd.to_datetime(group["date"], errors="coerce").dropna()
        product_summary[str(product)] = {
            "rows": int(len(group)),
            "symbols": sorted(group["symbol"].astype(str).unique().tolist()),
            "first_date": dates.min().date().isoformat(),
            "last_date": dates.max().date().isoformat(),
            "trading_days": int(dates.dt.date.nunique()),
        }

    report = {
        "source": "AKShare futures_zh_daily_sina -> Sina specific futures contracts",
        "start_date": START_DATE.date().isoformat(),
        "end_date": END_DATE.date().isoformat(),
        "generated_on": date.today().isoformat(),
        "products": product_summary,
        "symbol_status": status,
    }
    combined.to_csv(output / "two_year_specific_contract_daily.csv", index=False)
    (output / "realdata_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
