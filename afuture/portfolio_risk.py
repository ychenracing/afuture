"""组合级风险分析模块。

用于评估多个套利组合之间的风险重叠。
不直接修改交易仓位。
"""


class PortfolioRiskAnalyzer:
    """组合风险分析器。"""

    def __init__(self, max_correlated_exposure: float = 0.8):
        self.max_correlated_exposure = max_correlated_exposure

    def allow_new_pair(self, correlation: float, current_exposure: float) -> bool:
        """检查高度相关组合是否继续增加。"""
        if abs(correlation) >= self.max_correlated_exposure:
            return current_exposure < 1.0
        return True
