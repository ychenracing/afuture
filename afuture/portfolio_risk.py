"""组合级滚动相关性和风险组集中度控制。"""

from collections import defaultdict, deque
from math import sqrt

from .models import RiskDecision


class PortfolioRiskAnalyzer:
    """从价差序列自行计算相关性，不依赖外部手工输入。"""

    def __init__(
        self,
        window: int = 60,
        max_correlation: float = 0.8,
        min_samples: int = 20,
        max_group_open_pairs: int = 1,
    ) -> None:
        self.window = max(3, window)
        self.max_correlation = max_correlation
        self.min_samples = max(3, min_samples)
        self.max_group_open_pairs = max(1, max_group_open_pairs)
        self._series: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.window)
        )

    def update(self, pair_id: str, value: float) -> None:
        """追加一个价差观测。"""
        self._series[pair_id].append(float(value))

    def correlation(self, left: str, right: str) -> float:
        """按价差一阶变化计算滚动相关系数。"""
        left_values = list(self._series[left])
        right_values = list(self._series[right])
        sample_count = min(len(left_values), len(right_values))
        if sample_count < self.min_samples:
            return 0.0

        left_values = left_values[-sample_count:]
        right_values = right_values[-sample_count:]
        left_changes = [
            left_values[i] - left_values[i - 1]
            for i in range(1, sample_count)
        ]
        right_changes = [
            right_values[i] - right_values[i - 1]
            for i in range(1, sample_count)
        ]
        if len(left_changes) < 2:
            return 0.0

        left_mean = sum(left_changes) / len(left_changes)
        right_mean = sum(right_changes) / len(right_changes)
        left_var = sum(
            (value - left_mean) ** 2 for value in left_changes
        )
        right_var = sum(
            (value - right_mean) ** 2 for value in right_changes
        )
        if left_var <= 0 or right_var <= 0:
            return 0.0

        covariance = sum(
            (left_value - left_mean) * (right_value - right_mean)
            for left_value, right_value in zip(
                left_changes, right_changes
            )
        )
        return covariance / sqrt(left_var * right_var)

    def allow_open(
        self,
        pair_id: str,
        *,
        risk_group: str,
        open_pairs: dict[str, str],
    ) -> RiskDecision:
        """限制同风险组和高相关套利组合的同时暴露。"""
        if risk_group:
            group_count = sum(
                1
                for group in open_pairs.values()
                if group == risk_group
            )
            if group_count >= self.max_group_open_pairs:
                return RiskDecision(
                    False, "risk-group concentration limit reached"
                )

        for other_pair_id in open_pairs:
            if other_pair_id == pair_id:
                continue
            if abs(self.correlation(pair_id, other_pair_id)) >= self.max_correlation:
                return RiskDecision(
                    False,
                    f"pair correlation limit reached versus {other_pair_id}",
                )
        return RiskDecision(True)
