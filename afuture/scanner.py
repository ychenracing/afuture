"""跨期套利研究扫描器。"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log, sqrt

from .economics import estimate_net_edge
from .models import ContractSpec, PairConfig, SignalAction, Tick


@dataclass(frozen=True)
class SpreadCandidate:
    """单个跨期组合的研究候选评分。"""

    pair: str
    zscore: float
    liquidity_score: float
    volume_score: float
    open_interest_score: float
    half_life: float
    stationarity_score: float
    net_edge: float
    score: float


class SpreadScanner:
    """从 Tick 计算统计偏离、流动性、持仓活跃度和净边际。"""

    def __init__(
        self,
        min_liquidity_score: float = 0.5,
        slippage_ticks: int = 1,
    ) -> None:
        self.min_liquidity_score = min_liquidity_score
        self.slippage_ticks = slippage_ticks

    def filter(
        self, candidates: list[SpreadCandidate]
    ) -> list[SpreadCandidate]:
        """只保留流动性合格且预期净边际为正的候选。"""
        return [
            candidate
            for candidate in candidates
            if candidate.liquidity_score >= self.min_liquidity_score
            and candidate.net_edge > 0
        ]

    def scan_pair(
        self,
        pair: PairConfig,
        ticks: list[Tick],
        specs: dict[str, ContractSpec],
    ) -> SpreadCandidate | None:
        """对一个同品种跨期组合生成最新研究候选。"""
        near_by_time = {
            tick.timestamp: tick
            for tick in ticks
            if tick.symbol == pair.near_symbol
        }
        far_by_time = {
            tick.timestamp: tick
            for tick in ticks
            if tick.symbol == pair.far_symbol
        }
        common_times = sorted(set(near_by_time) & set(far_by_time))
        if len(common_times) < max(3, pair.lookback):
            return None

        spreads = [
            near_by_time[timestamp].mid_price
            - far_by_time[timestamp].mid_price
            for timestamp in common_times
        ]
        if len(spreads) > pair.lookback:
            history = spreads[-pair.lookback - 1 : -1]
        else:
            history = spreads[:-1]
        if len(history) < 2:
            return None

        mean = sum(history) / len(history)
        variance = sum((value - mean) ** 2 for value in history) / len(history)
        std = sqrt(variance)
        zscore = 0.0 if std <= 1e-12 else (spreads[-1] - mean) / std

        near = near_by_time[common_times[-1]]
        far = far_by_time[common_times[-1]]
        action = (
            SignalAction.SHORT_SPREAD
            if zscore > 0
            else SignalAction.LONG_SPREAD
        )
        edge = estimate_net_edge(
            action,
            reference_mean=mean,
            near=near,
            far=far,
            specs=specs,
            volume=pair.volume,
            slippage_ticks=self.slippage_ticks,
            legging_buffer=pair.legging_buffer,
        )

        depth = min(
            near.bid_volume,
            near.ask_volume,
            far.bid_volume,
            far.ask_volume,
        )
        liquidity_score = min(
            1.0,
            depth / max(pair.volume * 2.0, 1.0),
        )
        volume_score = 1.0 - exp(
            -max(min(near.volume, far.volume), 0.0) / 5000.0
        )
        open_interest_score = 1.0 - exp(
            -max(min(near.open_interest, far.open_interest), 0.0)
            / 10000.0
        )
        half_life, stationarity_score = self._mean_reversion_stats(spreads)

        activity_score = (
            0.25
            + 0.375 * volume_score
            + 0.375 * open_interest_score
        )
        score = (
            abs(zscore)
            * liquidity_score
            * activity_score
            * stationarity_score
            + max(edge.net_edge, 0.0) / 1000.0
        )
        return SpreadCandidate(
            pair=pair.pair_id,
            zscore=zscore,
            liquidity_score=liquidity_score,
            volume_score=volume_score,
            open_interest_score=open_interest_score,
            half_life=half_life,
            stationarity_score=stationarity_score,
            net_edge=edge.net_edge,
            score=score,
        )

    @staticmethod
    def _mean_reversion_stats(values: list[float]) -> tuple[float, float]:
        """用简单 AR(1) 近似计算半衰期和平稳性代理分数。"""
        if len(values) < 4:
            return 999.0, 0.0

        levels = values[:-1]
        changes = [
            values[index + 1] - values[index]
            for index in range(len(values) - 1)
        ]
        level_mean = sum(levels) / len(levels)
        change_mean = sum(changes) / len(changes)
        denominator = sum(
            (value - level_mean) ** 2 for value in levels
        )
        if denominator <= 1e-12:
            return 999.0, 0.0

        beta = sum(
            (level - level_mean) * (change - change_mean)
            for level, change in zip(levels, changes)
        ) / denominator
        if beta >= 0:
            return 999.0, 0.0

        half_life = max(0.1, -log(2.0) / beta)
        stationarity_score = min(1.0, max(0.0, -beta))
        return half_life, stationarity_score
