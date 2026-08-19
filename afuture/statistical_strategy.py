"""
跨期统计套利策略。

核心思想：
1. 计算近月与远月合约价差。
2. 使用历史均值和标准差判断异常偏离。
3. 当价差回归概率较高时产生套利信号。

该模块只负责研究信号，不负责真实交易下单。
"""

from statistics import mean, stdev


class SpreadStrategy:
    """价差均值回归策略。"""

    def __init__(self, 开仓阈值=2.0, 平仓阈值=0.5):
        self.开仓阈值 = 开仓阈值
        self.平仓阈值 = 平仓阈值

    def 计算_zscore(self, 价差序列):
        """计算当前价差距离历史均值的偏离程度。"""
        if len(价差序列) < 2:
            return 0
        波动 = stdev(价差序列)
        if 波动 == 0:
            return 0
        return (价差序列[-1] - mean(价差序列)) / 波动

    def 生成信号(self, 价差序列):
        """生成买入价差、卖出价差或等待信号。"""
        z = self.计算_zscore(价差序列)
        if z > self.开仓阈值:
            return "卖价差"
        if z < -self.开仓阈值:
            return "买价差"
        if abs(z) < self.平仓阈值:
            return "平仓"
        return "等待"
