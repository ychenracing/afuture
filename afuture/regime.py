"""轻量级套利关系稳定性、波动状态和曲线形态过滤。

本模块提炼三个外部研究方向中适合个人期货跨期系统的统计思想：

- 滚动关系持久性：避免只在一个幸运窗口里看起来均值回归；
- 波动率分位与变化点：极端波动或慢趋势切换时不逆势增加新风险；
- 归一化曲线 carry reversal：用近远月价格比消除绝对价格尺度影响。

实现只依赖 Python 标准库，不引入 EGARCH/GP/TensorFlow 等重依赖。所有指标只使用
调用时已经可见的序列；生产中它们只作为新开仓门/候选排序，不能阻止风险退出。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt

from .models import SignalAction


_MAX_Z = 12.0


@dataclass(frozen=True)
class EntryRegimeMetrics:
    """Auto 候选在当前时点的轻量 regime/carry 证据。"""

    persistence_score: float
    volatility_percentile: float
    trend_shift_z: float
    carry_z: float

    @property
    def supports_short(self) -> bool:
        """近月相对远月偏贵时，归一化 carry 支持做空价差。"""
        return self.carry_z > 0

    @property
    def supports_long(self) -> bool:
        """近月相对远月偏便宜时，归一化 carry 支持做多价差。"""
        return self.carry_z < 0


def carry_direction_strength(
    action: SignalAction,
    metrics: EntryRegimeMetrics,
) -> float:
    """返回 [0, 1] 的方向一致 carry 强度；冲突方向绝不获得排名奖励。"""
    if action is SignalAction.SHORT_SPREAD:
        aligned = metrics.carry_z if metrics.supports_short else 0.0
    elif action is SignalAction.LONG_SPREAD:
        aligned = -metrics.carry_z if metrics.supports_long else 0.0
    else:
        return 0.0
    return min(max(float(aligned), 0.0), 3.0) / 3.0


def evaluate_entry_regime(
    spreads: list[float],
    carries: list[float],
) -> EntryRegimeMetrics:
    """计算当前可见序列的持久性、波动状态、变化点和 carry 偏离。

    ``spreads`` 和 ``carries`` 的最后一个值代表当前已经可见的行情；历史均值、
    波动分布和 carry 基准均只使用最后值之前的数据，因此不会把未来 bar 泄漏给
    当前入场。短期波动分位可以包含“从上一可见值到当前值”的最新冲击，因为该
    价格变化在当前决策时已经发生。
    """
    spread_values = [float(value) for value in spreads]
    carry_values = [float(value) for value in carries]
    if len(spread_values) < 4:
        return EntryRegimeMetrics(0.0, 0.5, 0.0, 0.0)

    history = spread_values[:-1]
    current = spread_values[-1]
    persistence = _persistence_score(history)
    volatility_percentile = _volatility_percentile(spread_values)
    trend_shift_z = _trend_shift_z(history, current)
    carry_z = _latest_z(carry_values) if len(carry_values) >= 3 else 0.0
    return EntryRegimeMetrics(
        persistence_score=persistence,
        volatility_percentile=volatility_percentile,
        trend_shift_z=trend_shift_z,
        carry_z=carry_z,
    )


def normalized_curve_carry(near_prices: list[float], far_prices: list[float]) -> list[float]:
    """返回 log(near/far) 曲线形态；乘数缩放不会改变该信号。"""
    result: list[float] = []
    for near, far in zip(near_prices, far_prices):
        near = float(near)
        far = float(far)
        if near <= 0 or far <= 0:
            continue
        result.append(log(near / far))
    return result


def _persistence_score(values: list[float]) -> float:
    """用多个重叠子窗口的 AR(1) 均值回归方向作为关系持久性代理。"""
    if len(values) < 6:
        return 0.0
    window = max(5, min(len(values), len(values) // 2 + 1))
    step = max(1, window // 3)
    starts = list(range(0, max(1, len(values) - window + 1), step))
    last_start = max(0, len(values) - window)
    if last_start not in starts:
        starts.append(last_start)

    stable = 0
    total = 0
    for start in sorted(set(starts)):
        sample = values[start : start + window]
        beta = _ar1_beta(sample)
        total += 1
        if beta < 0:
            half_life = -log(2.0) / beta
            if 0 < half_life <= max(window * 4.0, 2.0):
                stable += 1
    return stable / total if total else 0.0


def _ar1_beta(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    levels = values[:-1]
    changes = [values[index + 1] - values[index] for index in range(len(values) - 1)]
    level_mean = sum(levels) / len(levels)
    change_mean = sum(changes) / len(changes)
    denominator = sum((value - level_mean) ** 2 for value in levels)
    if denominator <= 1e-12:
        return 0.0
    return sum(
        (level - level_mean) * (change - change_mean)
        for level, change in zip(levels, changes)
    ) / denominator


def _volatility_percentile(values: list[float]) -> float:
    """比较当前短期 RMS 变化与过去同长度波动分布，返回 [0, 1] 分位。"""
    if len(values) < 6:
        return 0.5
    changes = [values[index + 1] - values[index] for index in range(len(values) - 1)]
    short_window = max(2, min(5, len(changes) // 3))
    if len(changes) < short_window + 2:
        return 0.5

    rolling: list[float] = []
    for end in range(short_window, len(changes) + 1):
        sample = changes[end - short_window : end]
        rolling.append(sqrt(sum(value * value for value in sample) / len(sample)))
    current = rolling[-1]
    prior = rolling[:-1]
    if not prior:
        return 0.5
    return sum(value <= current for value in prior) / len(prior)


def _trend_shift_z(history: list[float], current: float) -> float:
    """短期水平相对较慢历史基准的绝对 Z，作为轻量 change-point 代理。"""
    if len(history) < 5:
        return 0.0
    short_window = max(2, min(5, len(history) // 3))
    recent = (history + [current])[-short_window:]
    reference = history[:-max(1, short_window // 2)]
    if len(reference) < 3:
        reference = history
    ref_mean = sum(reference) / len(reference)
    variance = sum((value - ref_mean) ** 2 for value in reference) / len(reference)
    std = sqrt(variance)
    recent_mean = sum(recent) / len(recent)
    delta = abs(recent_mean - ref_mean)
    if std <= 1e-12:
        return _MAX_Z if delta > 1e-12 else 0.0
    return min(_MAX_Z, delta / std)


def _latest_z(values: list[float]) -> float:
    history = values[:-1]
    if len(history) < 2:
        return 0.0
    mean = sum(history) / len(history)
    variance = sum((value - mean) ** 2 for value in history) / len(history)
    std = sqrt(variance)
    delta = values[-1] - mean
    if std <= 1e-12:
        if delta > 0:
            return _MAX_Z
        if delta < 0:
            return -_MAX_Z
        return 0.0
    return max(-_MAX_Z, min(_MAX_Z, delta / std))
