"""套利参数滚动校准辅助模块。

提供研究阶段的参数评估工具，不能直接覆盖生产参数。
避免因为单一区间优化导致过拟合。
"""


class ParameterCalibrator:
    """滚动参数评估器。"""

    def select_best(self, results: list[dict]) -> dict | None:
        """从候选结果中选择风险调整后较优结果。"""
        if not results:
            return None

        return max(
            results,
            key=lambda item: item.get("score", 0)
            - item.get("max_drawdown", 0),
        )
