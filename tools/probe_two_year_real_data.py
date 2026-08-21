"""Probe two years of real exchange futures daily data through AKShare.

This is research-only. It never enters the production trading path.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import akshare as ak
import pandas as pd

START_DATE = "20240821"
END_DATE = "20260820"
TARGETS = {
    "DCE": {"M", "C", "P"},
    "SHFE": {"RB"},
    "CZCE": {"TA"},
}


def _column(frame: pd.DataFrame, *names: str) -> str:
    lowered = {str(column).lower(): str(column) for column in frame.columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    raise KeyError(f"missing columns {names}; got {list(frame.columns)}")


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "source": "AKShare get_futures_daily -> Chinese futures exchange websites",
        "start_date": START_DATE,
        "end_date": END_DATE,
        "generated_on": date.today().isoformat(),
        "markets": {},
    }
    kept_frames: list[pd.DataFrame] = []

    for market, products in TARGETS.items():
        frame = ak.get_futures_daily(
            start_date=START_DATE,
            end_date=END_DATE,
            market=market,
        )
        variety_col = _column(frame, "variety")
        symbol_col = _column(frame, "symbol")
        date_col = _column(frame, "date")
        normalized_variety = frame[variety_col].astype(str).str.upper().str.strip()
        selected = frame.loc[normalized_variety.isin(products)].copy()
        selected["market"] = market
        selected["variety_norm"] = normalized_variety.loc[selected.index]
        kept_frames.append(selected)

        dates = pd.to_datetime(selected[date_col], errors="coerce").dropna()
        report["markets"][market] = {
            "requested_products": sorted(products),
            "available_varieties_sample": sorted(
                normalized_variety.dropna().unique().tolist()
            )[:80],
            "rows": int(len(selected)),
            "symbols": int(selected[symbol_col].astype(str).nunique()),
            "first_date": dates.min().date().isoformat() if not dates.empty else None,
            "last_date": dates.max().date().isoformat() if not dates.empty else None,
        }

    combined = pd.concat(kept_frames, ignore_index=True) if kept_frames else pd.DataFrame()
    if combined.empty:
        raise RuntimeError("no target futures rows were returned by the exchange data source")

    combined.to_csv(output / "two_year_exchange_daily.csv", index=False)
    (output / "realdata_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
