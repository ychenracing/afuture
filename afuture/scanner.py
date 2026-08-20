"""跨期套利机会扫描模块。

用于从多个合约价差中筛选研究候选，不自动开仓。
"""

from dataclasses import dataclass


@dataclass
class SpreadCandidate:
    """套利候选。"""

    pair: str
    zscore: float
    liquidity_score: float


class SpreadScanner:
    """扫描异常价差。"""

    def __init__(self, min_liquidity_score: float = 0.5):
        self.min_liquidity_score = min_liquidity_score

    def filter(self, candidates: list[SpreadCandidate]):
        """过滤流动性不足的候选。"""
        return [
            item
            for item in candidates
            if item.liquidity_score >= self.min_liquidity_score
        ]
