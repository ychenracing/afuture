"""Fetch a broad Chinese futures continuous daily universe for factor screening.

This is an L3 breadth screen, not final execution evidence. Any family that survives
must still be validated on specific contracts / CTP Shadow before production promotion.
"""
from __future__ import annotations

from pathlib import Path

import akshare as ak
import pandas as pd

START = pd.Timestamp("2022-08-22")
END = pd.Timestamp("2026-08-20")

# Mature, liquid commodity roots across DCE/CZCE/SHFE/INE. New or structurally
# illiquid contracts are intentionally omitted so breadth does not mean noise.
PRODUCTS = (
    # DCE
    "A", "B", "C", "CS", "EB", "EG", "I", "J", "JM", "L", "LH", "M",
    "P", "PG", "PP", "V", "Y",
    # CZCE
    "AP", "CF", "CJ", "FG", "MA", "OI", "PF", "PK", "RM", "SA", "SF",
    "SM", "SR", "TA", "UR",
    # SHFE
    "AG", "AL", "AU", "BU", "CU", "FU", "HC", "NI", "PB", "RB", "RU",
    "SN", "SP", "SS", "ZN",
    # INE
    "BC", "LU", "NR",
)

MIN_ROWS = 400


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    for product in PRODUCTS:
        symbol = f"{product}0"
        try:
            frame = ak.futures_zh_daily_sina(symbol=symbol).copy()
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
            continue
        if frame.empty:
            failures.append(f"{symbol}: empty")
            continue
        if "date" not in frame.columns or "close" not in frame.columns:
            failures.append(f"{symbol}: missing date/close columns")
            continue
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume", "hold", "settle"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.loc[
            (frame["date"] >= START) & (frame["date"] <= END)
        ].copy()
        frame.dropna(subset=["date", "close"], inplace=True)
        frame.drop_duplicates(["date"], keep="last", inplace=True)
        frame.sort_values("date", inplace=True)
        if len(frame) < MIN_ROWS:
            failures.append(f"{symbol}: only {len(frame)} rows in requested window")
            continue
        frame["product"] = product
        frame["symbol"] = symbol
        frames.append(frame)

    if not frames:
        raise RuntimeError("broad daily futures universe returned no usable history")

    data = pd.concat(frames, ignore_index=True)
    data.sort_values(["date", "product"], inplace=True)
    data.to_csv(output / "broad_daily_universe.csv", index=False)

    summary = (
        data.groupby("product")
        .agg(
            rows=("date", "size"),
            first=("date", "min"),
            last=("date", "max"),
            median_volume=("volume", "median"),
            median_hold=("hold", "median"),
        )
        .reset_index()
        .sort_values("product")
    )
    summary.to_csv(output / "broad_daily_universe_summary.csv", index=False)
    (output / "broad_daily_universe_failures.txt").write_text(
        "\n".join(failures), encoding="utf-8"
    )

    print(summary.to_string(index=False))
    print(f"\nusable_products={len(summary)} requested_products={len(PRODUCTS)}")
    if failures:
        print("\nUnavailable/insufficient roots (excluded from screen):")
        print("\n".join(failures))


if __name__ == "__main__":
    main()
