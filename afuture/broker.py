"""
交易接口抽象层。

该模块定义统一的交易接口，策略层不直接依赖具体交易柜台。
后续可以分别实现模拟交易接口和CTP真实交易接口。
"""

from abc import ABC, abstractmethod


class Broker(ABC):
    """交易接口基类。"""

    @abstractmethod
    def get_account(self):
        """获取账户资金信息。"""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self):
        """获取当前持仓。"""
        raise NotImplementedError

    @abstractmethod
    def send_order(self, order):
        """提交订单。"""
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id):
        """撤销订单。"""
        raise NotImplementedError
