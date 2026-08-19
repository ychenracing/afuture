from dataclasses import dataclass


@dataclass
class RiskConfig:
    max_margin_ratio: float = 0.3
    max_daily_loss_ratio: float = 0.01
    max_positions: int = 3


class RiskManager:
    def __init__(self, config=None):
        self.config = config or RiskConfig()

    def allow_open(self, equity, margin_used, positions):
        if positions >= self.config.max_positions:
            return False
        if equity <= 0:
            return False
        return margin_used / equity < self.config.max_margin_ratio

    def daily_loss_triggered(self, start_equity, current_equity):
        loss = (start_equity - current_equity) / start_equity
        return loss >= self.config.max_daily_loss_ratio
