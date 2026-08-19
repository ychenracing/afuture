"""
订单模型定义。

统一表示策略产生的交易请求，避免策略和执行层耦合。
"""

from dataclasses import dataclass


@dataclass
class Order:
    """期货订单对象。"""

    symbol: str
    direction: str
    volume: int
    price: float | None = None
    order_type: str = "limit"


@dataclass
class Trade:
    """成交记录对象。"""

    order_id: str
    symbol: str
    volume: int
    price: float
