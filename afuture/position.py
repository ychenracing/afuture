"""
持仓管理模块。

维护套利组合中的多腿持仓状态，为模拟交易和实盘同步提供统一模型。
"""


class Position:
    """单个合约持仓对象。"""

    def __init__(self, symbol, volume=0, avg_price=0.0):
        self.symbol = symbol
        self.volume = volume
        self.avg_price = avg_price

    def update(self, volume, price):
        """更新持仓数量和成本。"""
        total = self.volume + volume
        if total != 0:
            self.avg_price = (
                self.volume * self.avg_price + volume * price
            ) / total
        self.volume = total
