"""
账户级风险控制模块。

负责限制保证金使用比例、单品种风险暴露和账户异常状态。
该模块只负责风险判断，不负责生成交易订单。
"""


class AccountRiskManager:
    """管理交易账户风险规则。"""

    def __init__(self, max_margin_ratio=0.5, max_loss_ratio=0.02):
        self.max_margin_ratio = max_margin_ratio
        self.max_loss_ratio = max_loss_ratio

    def check_margin(self, margin, equity):
        """检查保证金占用是否超过限制。"""
        if equity <= 0:
            return False
        return margin / equity <= self.max_margin_ratio

    def check_daily_loss(self, daily_loss, equity):
        """检查当日亏损是否触发停止交易。"""
        if equity <= 0:
            return False
        return abs(daily_loss) / equity <= self.max_loss_ratio
