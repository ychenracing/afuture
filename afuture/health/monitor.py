"""交易系统健康监控。

只负责发现异常，不负责产生交易信号。
发现连接、行情、账户状态异常时，交由上层风控处理。
"""

from dataclasses import dataclass
from time import time


@dataclass
class HealthSnapshot:
    """系统健康快照。"""

    connected: bool
    market_delay_seconds: float
    account_ready: bool
    position_ready: bool


class HealthMonitor:
    """检查实时交易环境是否满足运行条件。"""

    def __init__(self, max_market_delay_seconds: float = 10):
        self.max_market_delay_seconds = max_market_delay_seconds

    def is_healthy(self, snapshot: HealthSnapshot) -> bool:
        """判断当前环境是否允许继续运行。"""
        return (
            snapshot.connected
            and snapshot.account_ready
            and snapshot.position_ready
            and snapshot.market_delay_seconds <= self.max_market_delay_seconds
        )
