from dataclasses import dataclass

from .spread import SpreadAnalyzer


@dataclass
class Trade:
    index: int
    action: str
    zscore: float


class SpreadBacktester:
    def __init__(self, analyzer=None):
        self.analyzer = analyzer or SpreadAnalyzer()

    def run(self, spreads):
        trades = []
        for i in range(len(spreads)):
            signal = self.analyzer.generate_signal(spreads[: i + 1])
            if signal.action != "HOLD":
                trades.append(Trade(i, signal.action, signal.zscore))
        return trades
