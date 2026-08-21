"""跨期套利研究扫描器。"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, log, sqrt

from .economics import estimate_net_edge, executable_spreads
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


@dataclass(frozen=True)
class SpreadStatistics:
    """不依赖手续费/保证金的纯行情统计预筛结果。"""

    zscore: float
    reference_mean: float
    reference_std: float
    half_life: float
    stationarity_score: float


class SpreadScanner:
    """从 Tick 计算统计偏离、流动性、持仓活跃度和净边际。"""

    def __init__(
        self,
        min_liquidity_score: float = 0.5,
        slippage_ticks: int = 1,
        max_sync_seconds: float = 2.0,
    ) -> None:
        if max_sync_seconds <= 0:
            raise ValueError("max_sync_seconds must be positive")
        self.min_liquidity_score = min_liquidity_score
        self.slippage_ticks = slippage_ticks
        self.max_sync_seconds = max_sync_seconds

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

    def entry_signal(
        self,
        pair: PairConfig,
        near: Tick,
        far: Tick,
        statistics: SpreadStatistics,
    ) -> tuple[SignalAction, float] | None:
        """用与生产策略一致的方向性可成交价差判断是否真正越过开仓阈值。"""
        long_spread, short_spread = executable_spreads(near, far)
        short_z = self._z(
            short_spread,
            statistics.reference_mean,
            statistics.reference_std,
        )
        long_z = self._z(
            long_spread,
            statistics.reference_mean,
            statistics.reference_std,
        )
        if short_z >= pair.entry_z:
            return SignalAction.SHORT_SPREAD, short_z
        if long_z <= -pair.entry_z:
            return SignalAction.LONG_SPREAD, long_z
        return None

    def scan_pair(
        self,
        pair: PairConfig,
        ticks: list[Tick],
        specs: dict[str, ContractSpec],
    ) -> SpreadCandidate | None:
        """对一个同品种跨期组合生成最新研究候选。"""
        synchronized = self.synchronized_ticks(pair, ticks)
        statistics = self.statistics(pair, ticks, synchronized=synchronized)
        if statistics is None:
            return None
        near, far = synchronized[-1]
        entry = self.entry_signal(pair, near, far, statistics)
        if entry is None:
            return None
        action, zscore = entry
        edge = estimate_net_edge(
            action,
            reference_mean=statistics.reference_mean,
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
        half_life = statistics.half_life
        stationarity_score = statistics.stationarity_score

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

    def statistics(
        self,
        pair: PairConfig,
        ticks: list[Tick],
        *,
        synchronized: list[tuple[Tick, Tick]] | None = None,
    ) -> SpreadStatistics | None:
        """只用行情计算统计预筛，避免对明显无机会组合查询 CTP 费率。"""
        synchronized = (
            synchronized
            if synchronized is not None
            else self.synchronized_ticks(pair, ticks)
        )
        if len(synchronized) < max(3, pair.lookback):
            return None
        spreads = [near.mid_price - far.mid_price for near, far in synchronized]
        history = (
            spreads[-pair.lookback - 1 : -1]
            if len(spreads) > pair.lookback
            else spreads[:-1]
        )
        if len(history) < 2:
            return None
        mean = sum(history) / len(history)
        variance = sum((value - mean) ** 2 for value in history) / len(history)
        std = sqrt(variance)
        zscore = self._z(spreads[-1], mean, std)
        half_life, stationarity_score = self._mean_reversion_stats(spreads)
        return SpreadStatistics(
            zscore=zscore,
            reference_mean=mean,
            reference_std=std,
            half_life=half_life,
            stationarity_score=stationarity_score,
        )

    def synchronized_ticks(
        self, pair: PairConfig, ticks: list[Tick]
    ) -> list[tuple[Tick, Tick]]:
        """按最近时间配对两腿行情，允许 CTP 异步推送存在小幅时间差。

        同一腿不会要求时间戳完全相同；超过 ``max_sync_seconds`` 的组合仍被拒绝。
        ``sample_seconds`` 同时用于降采样，避免高频 Tick 让统计窗口失去实际时间含义。
        """
        near_ticks = sorted(
            (tick for tick in ticks if tick.symbol == pair.near_symbol),
            key=lambda item: item.timestamp,
        )
        far_ticks = sorted(
            (tick for tick in ticks if tick.symbol == pair.far_symbol),
            key=lambda item: item.timestamp,
        )
        if not near_ticks or not far_ticks:
            return []

        result: list[tuple[Tick, Tick]] = []
        far_index = 0
        last_sample = None
        for near in near_ticks:
            while (
                far_index + 1 < len(far_ticks)
                and abs(
                    (far_ticks[far_index + 1].timestamp - near.timestamp).total_seconds()
                )
                <= abs(
                    (far_ticks[far_index].timestamp - near.timestamp).total_seconds()
                )
            ):
                far_index += 1
            far = far_ticks[far_index]
            skew = abs((far.timestamp - near.timestamp).total_seconds())
            if skew > self.max_sync_seconds:
                continue
            timestamp = max(near.timestamp, far.timestamp)
            if (
                last_sample is not None
                and pair.sample_seconds > 0
                and (timestamp - last_sample).total_seconds() < pair.sample_seconds
            ):
                continue
            result.append((near, far))
            last_sample = timestamp
        return result

    @staticmethod
    def _z(value: float, mean: float, std: float) -> float:
        delta = value - mean
        if std <= 1e-12:
            if delta > 0:
                return inf
            if delta < 0:
                return -inf
            return 0.0
        return delta / std

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
