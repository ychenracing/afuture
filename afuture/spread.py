from dataclasses import dataclass


@dataclass
class SpreadSignal:
    zscore: float
    action: str


class SpreadAnalyzer:
    def __init__(self, lookback: int = 60):
        self.lookback = lookback

    def calculate_zscore(self, spreads):
        if len(spreads) < self.lookback:
            return 0.0
        window = spreads[-self.lookback:]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        std = variance ** 0.5
        if std == 0:
            return 0.0
        return (spreads[-1] - mean) / std

    def generate_signal(self, spreads, entry=2.0, exit=0.5):
        z = self.calculate_zscore(spreads)
        if z > entry:
            return SpreadSignal(z, "SHORT_SPREAD")
        if z < -entry:
            return SpreadSignal(z, "LONG_SPREAD")
        if abs(z) < exit:
            return SpreadSignal(z, "EXIT")
        return SpreadSignal(z, "HOLD")
