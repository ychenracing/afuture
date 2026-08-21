"""参数稳定区域校准。

目标不是寻找历史最高分的孤立参数，而是优先选择周围参数也能维持相近表现的稳定区域。
"""

from __future__ import annotations


class ParameterCalibrator:
    """按邻域平均风险调整分选择稳定参数。"""

    def __init__(
        self, neighbor_radius: float = 0.20, min_neighbors: int = 2
    ) -> None:
        if neighbor_radius <= 0:
            raise ValueError("neighbor_radius must be positive")
        if min_neighbors <= 0:
            raise ValueError("min_neighbors must be positive")
        self.neighbor_radius = neighbor_radius
        self.min_neighbors = min_neighbors

    def select_best(self, results: list[dict]) -> dict | None:
        """返回稳定区域中心；多个候选均孤立时拒绝选出“历史冠军”。"""
        if not results:
            return None
        if len(results) == 1:
            return results[0]

        stable_candidates: list[tuple[float, float, dict]] = []
        for row in results:
            neighbors = [
                other for other in results if self._neighbor(row, other)
            ]
            if len(neighbors) < self.min_neighbors:
                continue
            neighborhood_score = sum(
                self._risk_adjusted(item) for item in neighbors
            ) / len(neighbors)
            own_score = self._risk_adjusted(row)
            stable_candidates.append(
                (neighborhood_score, own_score, row)
            )

        if not stable_candidates:
            return None
        return max(stable_candidates, key=lambda item: (item[0], item[1]))[2]

    def _neighbor(self, left: dict, right: dict) -> bool:
        keys = [
            key
            for key in ("lookback", "entry_z", "exit_z", "stop_z")
            if key in left and key in right
        ]
        if not keys:
            return left is right
        for key in keys:
            left_value = float(left[key])
            right_value = float(right[key])
            scale = max(abs(left_value), abs(right_value), 1e-9)
            if abs(left_value - right_value) / scale > self.neighbor_radius:
                return False
        return True

    @staticmethod
    def _risk_adjusted(row: dict) -> float:
        drawdown = abs(float(row.get("max_drawdown", 0.0)))
        score = float(row.get("score", row.get("sharpe", 0.0)))
        return score - drawdown
