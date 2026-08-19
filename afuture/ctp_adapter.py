"""
CTP交易接口适配层。

当前提供接口定义和扩展结构。
实际连接需要根据期货公司提供的CTP环境接入对应SDK。
"""


class CtpAdapter:
    """CTP接口抽象。"""

    def connect(self):
        """建立交易连接。"""
        raise NotImplementedError

    def subscribe_market_data(self, symbols):
        """订阅行情。"""
        raise NotImplementedError

    def send_order(self, order):
        """发送订单。"""
        raise NotImplementedError

    def cancel_order(self, order_id):
        """撤销订单。"""
        raise NotImplementedError
