"""CSV 行情读取与回放数据校验。"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import Tick


_REQUIRED_COLUMNS = {
    "timestamp",
    "symbol",
    "exchange",
    "bid_price",
    "ask_price",
    "last_price",
    "bid_volume",
    "ask_volume",
    "trading_day",
}


def read_ticks(path: str | Path) -> list[Tick]:
    """读取标准化 Tick CSV，并按时间排序。"""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        ticks = [
            Tick(
                symbol=row["symbol"],
                exchange=row["exchange"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                bid_price=float(row["bid_price"]),
                ask_price=float(row["ask_price"]),
                last_price=float(row["last_price"]),
                bid_volume=float(row["bid_volume"]),
                ask_volume=float(row["ask_volume"]),
                trading_day=row["trading_day"],
                limit_up=float(row.get("limit_up") or 0.0),
                limit_down=float(row.get("limit_down") or 0.0),
            )
            for row in reader
        ]
    for tick in ticks:
        tick.validate()
    return sorted(ticks, key=lambda item: item.timestamp)
