"""Fetch targeted 60-minute specific-contract history for intraday pair research."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time

import akshare as ak
import pandas as pd

from fetch_specific_pair_daily_universe import END, START, contract_symbols, delivery_date

PRODUCTS = {"BU", "FU", "PP", "V"}
MAX_WORKERS = 8


def _download(symbol: str) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            frame = ak.futures_zh_minute_sina(symbol=symbol, period="60").copy()
            if not frame.empty:
                return frame
        except Exception as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(0.20 * (2**attempt))
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def _fetch_one(item: tuple[str, str, str]):
    product, exchange, symbol = item
    try:
        frame = _download(symbol)
    except Exception as exc:
        return None, f"{symbol}: {type(exc).__name__}: {exc}"
    if frame.empty:
        return None, f"{symbol}: empty"
    column = "datetime" if "datetime" in frame.columns else str(frame.columns[0])
    frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for name in ("open", "high", "low", "close", "volume", "hold"):
        if name in frame.columns:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.loc[
        (frame[column] >= START) & (frame[column] <= END + pd.Timedelta(hours=23, minutes=59))
    ].copy()
    frame.rename(columns={column: "datetime"}, inplace=True)
    frame.dropna(subset=["datetime", "close", "hold"], inplace=True)
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return None, None
    frame["symbol"] = symbol
    frame["product"] = product
    frame["exchange"] = exchange
    frame["delivery"] = delivery_date(symbol)
    return frame, None


def main() -> None:
    output = Path("runtime")
    output.mkdir(parents=True, exist_ok=True)
    requests = [item for item in contract_symbols() if item[0] in PRODUCTS]
    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, item): item for item in requests}
        completed = 0
        for future in as_completed(futures):
            frame, failure = future.result()
            completed += 1
            if frame is not None:
                frames.append(frame)
            if failure:
                failures.append(failure)
            if completed % 25 == 0 or completed == len(requests):
                print(
                    f"downloaded={completed}/{len(requests)} usable={len(frames)} "
                    f"failures={len(failures)}",
                    flush=True,
                )

    if not frames:
        raise RuntimeError("targeted 60-minute specific-contract fetch returned no data")
    data = pd.concat(frames, ignore_index=True)
    data.drop_duplicates(["product", "symbol", "datetime"], keep="last", inplace=True)
    data.sort_values(["datetime", "product", "symbol"], inplace=True)
    data.to_csv(output / "specific_pair_60m_contracts.csv", index=False)

    summary = (
        data.groupby("product")
        .agg(
            rows=("datetime", "size"),
            contracts=("symbol", "nunique"),
            trading_days=("datetime", lambda values: values.dt.normalize().nunique()),
            first=("datetime", "min"),
            last=("datetime", "max"),
        )
        .reset_index()
        .sort_values("product")
    )
    summary.to_csv(output / "specific_pair_60m_summary.csv", index=False)
    (output / "specific_pair_60m_failures.txt").write_text(
        "\n".join(sorted(failures)), encoding="utf-8"
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
