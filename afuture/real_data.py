"""两年真实期货研究数据适配。

Sina 日线提供真实 OHLC、成交量、持仓量和结算价；公开历史接口不提供两年完整
L1 bid/ask 深度，因此本模块把盘口相关字段明确建模为保守执行代理，禁止把它们
描述成真实历史盘口。生产实盘仍只使用 CTP 实时 L1。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
from math import sqrt
import re
from time import sleep
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import ContractInfo, ContractSpec, FeeSpec, Tick


_CHINA_TZ = ZoneInfo("Asia/Shanghai")
_EXECUTION_PROXY = "close +/- one price tick; historical L1 unavailable"
_ALL_MONTHS = tuple(range(1, 13))


@dataclass(frozen=True)
class DailyBar:
    """新浪合约日线原始字段。"""

    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_interest: float
    settle: float


@dataclass(frozen=True)
class ProductDefinition:
    """研究所需的稳定合约规格和保守费用假设。"""

    product: str
    exchange: str
    multiplier: float
    price_tick: float
    margin_rate: float
    open_fee: float = 0.0
    close_fee: float = 0.0
    close_today_fee: float | None = None
    fee_rate: float = 0.0
    contract_months: tuple[int, ...] = (1, 5, 9)


@dataclass(frozen=True)
class DailyTickConversion:
    ticks: list[Tick]
    execution_proxy: str = _EXECUTION_PROXY


PRODUCT_DEFINITIONS: dict[str, ProductDefinition] = {
    # 月份按交易所真实挂牌周期生成；是否进入候选由真实 volume/OI/流动性门决定，
    # 不能在研究阶段只保留“历史上看起来最好”的主力月。
    # 手续费取公开交易所/行情页常见标准，研究另做 1.5x/2x 成本压力。
    "m": ProductDefinition(
        "m", "DCE", 10, 1, 0.15, 1.5, 1.5, 1.5,
        contract_months=(1, 3, 5, 7, 8, 9, 11, 12),
    ),
    "rb": ProductDefinition(
        "rb", "SHFE", 10, 1, 0.15, fee_rate=0.0001,
        contract_months=_ALL_MONTHS,
    ),
    "TA": ProductDefinition(
        "TA", "CZCE", 5, 2, 0.15, 3.0, 3.0, 0.0,
        contract_months=_ALL_MONTHS,
    ),
    "c": ProductDefinition(
        "c", "DCE", 10, 1, 0.15, 1.2, 1.2, 1.2,
        contract_months=(1, 3, 5, 7, 9, 11),
    ),
    "p": ProductDefinition(
        "p", "DCE", 10, 2, 0.15, 2.5, 2.5, 2.5,
        contract_months=_ALL_MONTHS,
    ),
}


def parse_sina_daily_jsonp(payload: str, symbol: str) -> list[DailyBar]:
    """解析 Sina DailyKLine JSONP；同时兼容二维数组和字典行。"""

    left = payload.find("(")
    right = payload.rfind(")")
    if left < 0 or right <= left:
        raw_text = payload.strip().rstrip(";")
    else:
        raw_text = payload[left + 1 : right]
    raw = json.loads(raw_text)
    if raw is None:
        return []
    rows: list[DailyBar] = []
    for item in raw:
        if isinstance(item, dict):
            values = (
                item.get("d") or item.get("date"),
                item.get("o") or item.get("open"),
                item.get("h") or item.get("high"),
                item.get("l") or item.get("low"),
                item.get("c") or item.get("close"),
                item.get("v") or item.get("volume"),
                item.get("p") or item.get("hold") or item.get("open_interest"),
                item.get("s") or item.get("settle") or item.get("settlement") or 0,
            )
        else:
            if len(item) < 7:
                continue
            values = tuple(item[:8]) if len(item) >= 8 else tuple(item[:7]) + (0,)
        try:
            row = DailyBar(
                symbol=symbol.upper(),
                day=date.fromisoformat(str(values[0])[:10]),
                open=float(values[1]),
                high=float(values[2]),
                low=float(values[3]),
                close=float(values[4]),
                volume=float(values[5]),
                open_interest=float(values[6]),
                settle=float(values[7] or 0),
            )
        except (TypeError, ValueError):
            continue
        if row.close <= 0 or row.high <= 0 or row.low <= 0:
            continue
        if row.volume < 0 or row.open_interest < 0:
            continue
        rows.append(row)
    rows.sort(key=lambda row: row.day)
    return rows


class SinaDailyClient:
    """只用于研究的数据下载器；带低频重试，避免无边界请求公开接口。"""

    def __init__(self, timeout_seconds: float = 20.0, retries: int = 3) -> None:
        if timeout_seconds <= 0 or retries <= 0:
            raise ValueError("timeout_seconds and retries must be positive")
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def fetch(self, symbol: str) -> list[DailyBar]:
        symbol = symbol.upper()
        params = urlencode({"symbol": symbol, "type": "2021_04_12"})
        urls = (
            f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}2021_4_12=/InnerFuturesNewService.getDailyKLine?{params}",
            f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php//InnerFuturesNewService.getDailyKLine?symbol={symbol}",
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            for url in urls:
                request = Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 afuture-research/1.0",
                        "Referer": f"https://finance.sina.com.cn/futures/quotes/{symbol}.shtml",
                    },
                )
                try:
                    with urlopen(request, timeout=self.timeout_seconds) as response:
                        text = response.read().decode("utf-8", errors="replace")
                    rows = parse_sina_daily_jsonp(text, symbol)
                    if rows:
                        return rows
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                    last_error = exc
            sleep(0.5 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError(f"Sina daily fetch failed for {symbol}: {last_error}") from last_error
        return []


def contract_symbols(
    definition: ProductDefinition,
    start: date,
    end: date,
    *,
    warmup_days: int = 365,
    far_month_buffer: int = 8,
) -> list[str]:
    """生成研究窗口附近的真实挂牌月代码，不访问未来行情。"""

    first = start - timedelta(days=max(0, warmup_days))
    last = end + timedelta(days=max(0, far_month_buffer) * 31)
    result: list[str] = []
    for year in range(first.year, last.year + 1):
        for month in definition.contract_months:
            contract_day = date(year, month, 1)
            if contract_day < date(first.year, first.month, 1):
                continue
            if contract_day > date(last.year, last.month, 1):
                continue
            result.append(f"{definition.product.upper()}{year % 100:02d}{month:02d}")
    return result


def contract_spec(symbol: str, definition: ProductDefinition) -> ContractSpec:
    close_today = definition.close_fee if definition.close_today_fee is None else definition.close_today_fee
    fee = FeeSpec(
        open_fixed=definition.open_fee,
        open_rate=definition.fee_rate,
        close_fixed=definition.close_fee,
        close_rate=definition.fee_rate,
        close_today_fixed=close_today,
        close_today_rate=definition.fee_rate,
    )
    return ContractSpec(
        symbol=symbol.upper(),
        exchange=definition.exchange,
        multiplier=definition.multiplier,
        price_tick=definition.price_tick,
        margin_rate_long=definition.margin_rate,
        margin_rate_short=definition.margin_rate,
        fee=fee,
    )


def daily_bars_to_ticks(
    bars: Iterable[DailyBar],
    definition: ProductDefinition,
) -> DailyTickConversion:
    """把真实日线转换为生产模型可消费的保守日频研究 Tick。

    close/volume/OI 仍是原始真实数据；bid/ask 和一档深度只是执行压力代理。
    """

    ticks: list[Tick] = []
    for row in bars:
        if row.volume <= 0 or row.open_interest <= 0:
            continue
        bid = row.close - definition.price_tick
        ask = row.close + definition.price_tick
        if bid <= 0:
            continue
        depth = max(1.0, min(200.0, sqrt(max(row.volume, 1.0)) / 2.0))
        timestamp = datetime.combine(row.day, time(14, 59), tzinfo=_CHINA_TZ)
        tick = Tick(
            symbol=row.symbol.upper(),
            exchange=definition.exchange,
            timestamp=timestamp,
            bid_price=bid,
            ask_price=ask,
            last_price=row.close,
            bid_volume=depth,
            ask_volume=depth,
            trading_day=row.day.strftime("%Y%m%d"),
            volume=row.volume,
            open_interest=row.open_interest,
        )
        tick.validate()
        ticks.append(tick)
    return DailyTickConversion(ticks=ticks)


def infer_contract_info(
    bars_by_symbol: dict[str, list[DailyBar]],
    definitions: dict[str, ProductDefinition] | None = None,
) -> list[ContractInfo]:
    """用合约真实最后有行情日期作为已到期合约研究 expiry。"""

    definitions = definitions or PRODUCT_DEFINITIONS
    result: list[ContractInfo] = []
    for symbol, bars in sorted(bars_by_symbol.items()):
        if not bars:
            continue
        match = re.match(r"([A-Za-z]+)", symbol)
        root = match.group(1) if match else ""
        definition = next(
            (row for key, row in definitions.items() if key.lower() == root.lower()),
            None,
        )
        if definition is None:
            continue
        result.append(
            ContractInfo(
                symbol=symbol.upper(),
                exchange=definition.exchange,
                product=definition.product,
                expiry=max(row.day for row in bars).isoformat(),
            )
        )
    return result
