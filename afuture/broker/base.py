"""交易柜台统一接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AccountSnapshot, BrokerEvent, ContractPosition, Order, OrderRequest, Tick


class Broker(ABC):
    """回放、模拟和 CTP 实盘都遵守同一接口。"""

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_ready(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, symbol: str, exchange: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_order(self, request: OrderRequest) -> str:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[ContractPosition]:
        raise NotImplementedError

    @abstractmethod
    def get_active_orders(self) -> list[Order]:
        raise NotImplementedError

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None:
        raise NotImplementedError

    @abstractmethod
    def poll_events(self) -> list[BrokerEvent]:
        raise NotImplementedError

    def owns_order(self, order_id: str) -> bool:
        """判断成交是否来自本进程提交的订单；实盘适配器应使用更严格的实现。"""
        return self.get_order(order_id) is not None

    def health_error(self) -> str | None:
        """返回柜台健康异常；模拟柜台默认没有异步健康状态。"""
        return None

    def publish_tick(self, tick: Tick) -> None:
        """仅模拟柜台需要主动注入行情。"""
        raise NotImplementedError

    def get_trading_day(self) -> str:
        return self.get_account().trading_day
