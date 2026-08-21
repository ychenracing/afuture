"""最终 Auto Portfolio 的预注册晋级门。

阈值在查看最终 OOS 前固定，避免为了历史结果移动球门。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutoPortfolioAcceptanceDecision:
    accepted: bool
    reasons: tuple[str, ...]
    metrics: dict


class AutoPortfolioAcceptanceGate:
    def __init__(
        self,
        *,
        min_positive_oos_ratio: float = 0.60,
        max_oos_drawdown: float = 0.06,
        min_stress_return: float = -0.02,
        max_leave_one_loss: float = -0.05,
        max_single_product_concentration: float = 0.70,
        min_oos_trade_legs: int = 4,
    ) -> None:
        self.min_positive_oos_ratio = min_positive_oos_ratio
        self.max_oos_drawdown = max_oos_drawdown
        self.min_stress_return = min_stress_return
        self.max_leave_one_loss = max_leave_one_loss
        self.max_single_product_concentration = max_single_product_concentration
        self.min_oos_trade_legs = min_oos_trade_legs

    def evaluate(self, result) -> AutoPortfolioAcceptanceDecision:
        reasons: list[str] = []
        folds = list(result.folds)
        if not folds:
            reasons.append("no auto portfolio walk-forward folds")
            positive_ratio = 0.0
            worst_drawdown = 0.0
            trade_legs = 0
        else:
            positive_ratio = sum(
                float(fold.oos_metrics.get("total_return", 0.0)) > 0
                for fold in folds
            ) / len(folds)
            worst_drawdown = max(
                abs(float(fold.oos_metrics.get("max_drawdown", 0.0)))
                for fold in folds
            )
            trade_legs = sum(int(fold.oos_metrics.get("trade_count", 0)) for fold in folds)
            if positive_ratio < self.min_positive_oos_ratio:
                reasons.append("positive OOS fold ratio is too low")
            if worst_drawdown > self.max_oos_drawdown:
                reasons.append("worst OOS drawdown exceeds preregistered limit")
            if trade_legs < self.min_oos_trade_legs:
                reasons.append("OOS trade sample is too small")

        worst_stress = min(
            (float(row.get("total_return", 0.0)) for row in result.stress_results.values()),
            default=0.0,
        )
        if worst_stress < self.min_stress_return:
            reasons.append("cost stress is too fragile")

        leave_one = result.robustness.get("leave_one_product_out", {}) or {}
        if len(leave_one) > 1 and any(
            float(row.get("total_return", 0.0)) < self.max_leave_one_loss
            for row in leave_one.values()
        ):
            reasons.append("leave-one-product-out shows catastrophic dependence")

        single = result.robustness.get("single_product", {}) or {}
        positives = [max(0.0, float(row.get("total_return", 0.0))) for row in single.values()]
        positive_total = sum(positives)
        concentration = max(positives, default=0.0) / positive_total if positive_total > 0 else 0.0
        if len(positives) > 1 and concentration > self.max_single_product_concentration:
            reasons.append("single-product attribution is too concentrated")

        metrics = {
            "positive_oos_ratio": positive_ratio,
            "worst_oos_drawdown": worst_drawdown,
            "oos_trade_legs": trade_legs,
            "worst_cost_stress_return": worst_stress,
            "single_product_positive_return_concentration": concentration,
        }
        return AutoPortfolioAcceptanceDecision(not reasons, tuple(reasons), metrics)
