"""模拟交易模块。

用于在连接真实交易所之前验证订单、资金和持仓逻辑。
"""

from dataclasses import dataclass, field


@dataclass
class 模拟账户:
    """简单模拟期货账户。"""

    初始资金: float = 100000
    可用资金: float = field(init=False)
    持仓: dict = field(default_factory=dict)

    def __post_init__(self):
        self.可用资金 = self.初始资金

    def 开仓(self, 合约: str, 数量: int, 价格: float):
        """记录开仓，不连接真实交易接口。"""
        self.持仓[合约] = self.持仓.get(合约, 0) + 数量

    def 平仓(self, 合约: str, 数量: int):
        """记录平仓。"""
        当前 = self.持仓.get(合约, 0)
        self.持仓[合约] = max(0, 当前 - 数量)
