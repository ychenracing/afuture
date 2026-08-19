"""
期货行情数据模块。

当前阶段用于研究和回测，支持读取标准化后的历史行情数据。
后续可以接入交易所行情接口。
"""

from dataclasses import dataclass
from typing import List


@dataclass
class FuturesBar:
    """期货K线数据。"""

    date: str
    contract: str
    close: float


class MarketData:
    """市场数据读取接口。"""

    def __init__(self, bars: List[FuturesBar]):
        self.bars = bars

    def prices(self, contract: str):
        """获取指定合约收盘价序列。"""
        return [bar.close for bar in self.bars if bar.contract == contract]
