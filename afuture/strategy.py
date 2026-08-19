"""商品期货跨期价差均值回归策略。"""

from __future__ import annotations

from collections import deque
from math import sqrt

from .models import PairConfig, SignalAction, SpreadSignal, Tick


class CalendarSpreadStrategy:
    """只产生价差信号，不直接访问交易柜台。"""

    def __init__(self, pair: PairConfig) -> None:
        if pair.lookback < 2:
            raise ValueError("lookback must be at least 2")
        if not 0 <= pair.exit_z < pair.entry_z < pair.stop_z:
            raise ValueError("z-score thresholds must satisfy exit < entry < stop")
        self.pair = pair
        self._history: deque[float] = deque(maxlen=pair.lookback)
        self._position = 0
        self._entry_mean = 0.0
        self._entry_std = 0.0
        self._last_sample_ts = None

    @property
    def position(self) -> int:
        """1 表示多价差，-1 表示空价差，0 表示无仓。"""
        return self._position

    def set_position(self, position: int) -> None:
        if position not in {-1, 0, 1}:
            raise ValueError("spread position must be -1, 0 or 1")
        self._position = position

    def snapshot_state(self) -> dict:
        """导出策略状态，保证重启后不丢失滚动窗口和入场基准。"""
        return {
            "history": list(self._history),
            "position": self._position,
            "entry_mean": self._entry_mean,
            "entry_std": self._entry_std,
            "last_sample_ts": self._last_sample_ts.isoformat() if self._last_sample_ts else "",
        }

    def restore_state(self, state: dict) -> None:
        """恢复由 snapshot_state 生成的状态；超长历史自动截断到当前 lookback。"""
        self._history.clear()
        for value in state.get("history", [])[-self.pair.lookback:]:
            self._history.append(float(value))
        self.set_position(int(state.get("position", 0)))
        self._entry_mean = float(state.get("entry_mean", 0.0))
        self._entry_std = float(state.get("entry_std", 0.0))
        raw_ts = str(state.get("last_sample_ts", ""))
        if raw_ts:
            from datetime import datetime
            self._last_sample_ts = datetime.fromisoformat(raw_ts)
        else:
            self._last_sample_ts = None

    def on_quotes(self, near: Tick, far: Tick) -> SpreadSignal:
        """用可成交盘口中间价构造价差并生成状态化信号。"""
        near.validate()
        far.validate()
        timestamp = max(near.timestamp, far.timestamp)
        spread = near.mid_price - far.mid_price

        if self._last_sample_ts is not None and self.pair.sample_seconds > 0:
            elapsed = (timestamp - self._last_sample_ts).total_seconds()
            if elapsed < self.pair.sample_seconds:
                return SpreadSignal(self.pair.pair_id, SignalAction.HOLD, 0.0, timestamp, spread, 0.0)

        if len(self._history) < self.pair.lookback:
            self._history.append(spread)
            self._last_sample_ts = timestamp
            return SpreadSignal(self.pair.pair_id, SignalAction.HOLD, 0.0, timestamp, spread, self._mean())

        mean = self._mean()
        std = self._std(mean)
        zscore = 0.0 if std == 0 else (spread - mean) / std
        action = SignalAction.HOLD

        if self._position == 0:
            if zscore >= self.pair.entry_z:
                action = SignalAction.SHORT_SPREAD
                self._position = -1
                self._entry_mean = mean
                self._entry_std = max(std, 1e-12)
            elif zscore <= -self.pair.entry_z:
                action = SignalAction.LONG_SPREAD
                self._position = 1
                self._entry_mean = mean
                self._entry_std = max(std, 1e-12)
        else:
            entry_z = (spread - self._entry_mean) / max(self._entry_std, 1e-12)
            if abs(zscore) >= self.pair.stop_z:
                action = SignalAction.EMERGENCY_EXIT
                self._position = 0
            elif abs(entry_z) <= self.pair.exit_z:
                action = SignalAction.EXIT
                self._position = 0

        self._history.append(spread)
        self._last_sample_ts = timestamp
        return SpreadSignal(self.pair.pair_id, action, zscore, timestamp, spread, mean)

    def _mean(self) -> float:
        return sum(self._history) / len(self._history) if self._history else 0.0

    def _std(self, mean: float) -> float:
        if not self._history:
            return 0.0
        variance = sum((value - mean) ** 2 for value in self._history) / len(self._history)
        return sqrt(variance)
