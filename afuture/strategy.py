"""商品期货跨期价差均值回归策略。"""

from __future__ import annotations

from collections import deque
from math import inf, sqrt

from .economics import executable_spreads
from .models import PairConfig, SignalAction, SpreadSignal, Tick


class CalendarSpreadStrategy:
    """只产生统计交易意图，不直接访问柜台或决定最终能否开仓。"""

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
        self._holding_samples = 0
        self._last_sample_ts = None

    @property
    def position(self) -> int:
        """1 表示多价差，-1 表示空价差，0 表示无仓。"""
        return self._position

    @property
    def spread_std(self) -> float:
        mean = self._mean()
        return self._std(mean)

    def set_position(self, position: int) -> None:
        if position not in {-1, 0, 1}:
            raise ValueError("spread position must be -1, 0 or 1")
        if self._position == 0 and position != 0 and self._history:
            self._entry_mean = self._mean()
            self._entry_std = max(self._std(self._entry_mean), 1e-9)
            self._holding_samples = 0
        if position == 0:
            self._holding_samples = 0
        self._position = position

    def snapshot_state(self) -> dict:
        """导出策略状态，保证重启后不丢失滚动窗口和入场锚点。"""
        return {
            "history": list(self._history),
            "position": self._position,
            "entry_mean": self._entry_mean,
            "entry_std": self._entry_std,
            "holding_samples": self._holding_samples,
            "last_sample_ts": self._last_sample_ts.isoformat() if self._last_sample_ts else "",
        }

    def restore_state(self, state: dict) -> None:
        self._history.clear()
        for value in state.get("history", [])[-self.pair.lookback:]:
            self._history.append(float(value))
        self._position = int(state.get("position", 0))
        if self._position not in {-1, 0, 1}:
            raise ValueError("invalid persisted spread position")
        self._entry_mean = float(state.get("entry_mean", 0.0))
        self._entry_std = float(state.get("entry_std", 0.0))
        self._holding_samples = int(state.get("holding_samples", 0))
        raw_ts = str(state.get("last_sample_ts", ""))
        if raw_ts:
            from datetime import datetime
            self._last_sample_ts = datetime.fromisoformat(raw_ts)
        else:
            self._last_sample_ts = None

    def restore_after_rejected_signal(self, previous_state: dict) -> None:
        """执行未接受信号时恢复真实持仓语义，同时保留本次市场观测。

        ``on_quotes`` 为避免同一 Tick 重复发单会乐观更新内部目标仓位。若随后被
        风控或执行层拒绝，不能简单 ``set_position``，否则已有持仓的入场均值和
        波动锚点会按当前市场重建。这里恢复信号前的持仓/锚点，但保留已经追加的
        最新价差样本和采样时间；已有仓位的持有样本数继续前进一格。
        """
        current_history = list(self._history)
        current_last_sample_ts = self._last_sample_ts
        previous_position = int(previous_state.get("position", 0))
        previous_holding = int(previous_state.get("holding_samples", 0))

        self.restore_state(previous_state)
        self._history.clear()
        for value in current_history[-self.pair.lookback:]:
            self._history.append(float(value))
        self._last_sample_ts = current_last_sample_ts
        if previous_position != 0:
            self._holding_samples = previous_holding + 1

    def on_quotes(self, near: Tick, far: Tick) -> SpreadSignal:
        """历史中心用中间价维护，入场和持仓退出使用方向性可成交价差判断。"""
        near.validate()
        far.validate()
        timestamp = max(near.timestamp, far.timestamp)
        mid_spread = near.mid_price - far.mid_price

        if self._last_sample_ts is not None and self.pair.sample_seconds > 0:
            if (timestamp - self._last_sample_ts).total_seconds() < self.pair.sample_seconds:
                return SpreadSignal(
                    self.pair.pair_id,
                    SignalAction.HOLD,
                    0.0,
                    timestamp,
                    mid_spread,
                    self._mean(),
                    self.spread_std,
                )

        if len(self._history) < self.pair.lookback:
            self._history.append(mid_spread)
            self._last_sample_ts = timestamp
            return SpreadSignal(
                self.pair.pair_id,
                SignalAction.HOLD,
                0.0,
                timestamp,
                mid_spread,
                self._mean(),
                self.spread_std,
            )

        mean = self._mean()
        std = self._std(mean)
        long_exec, short_exec = executable_spreads(near, far)
        long_z = self._z(long_exec, mean, std)
        short_z = self._z(short_exec, mean, std)
        mid_z = self._z(mid_spread, mean, std)
        action = SignalAction.HOLD
        chosen_spread = mid_spread
        chosen_z = mid_z
        reason = ""

        if self._position == 0:
            if short_z >= self.pair.entry_z:
                action = SignalAction.SHORT_SPREAD
                chosen_spread, chosen_z = short_exec, short_z
                self._position = -1
            elif long_z <= -self.pair.entry_z:
                action = SignalAction.LONG_SPREAD
                chosen_spread, chosen_z = long_exec, long_z
                self._position = 1
            if action is not SignalAction.HOLD:
                self._entry_mean = mean
                self._entry_std = max(std, 1e-9)
                self._holding_samples = 0
        else:
            self._holding_samples += 1
            # 平多价差时卖近买远，可实现价差是 short_exec；平空价差反之。
            liquidation_spread = short_exec if self._position > 0 else long_exec
            liquidation_z = (
                liquidation_spread - self._entry_mean
            ) / max(self._entry_std, 1e-9)
            chosen_spread, chosen_z = liquidation_spread, liquidation_z
            current_std = max(std, 1e-9)
            mean_shift_z = max(
                abs(mean - self._entry_mean),
                abs(mid_spread - self._entry_mean),
            ) / max(self._entry_std, 1e-9)
            vol_ratio = current_std / max(self._entry_std, 1e-9)
            reverted = (
                self._position > 0 and liquidation_z >= -self.pair.exit_z
            ) or (
                self._position < 0 and liquidation_z <= self.pair.exit_z
            )
            structural_break = (
                mean_shift_z >= self.pair.structural_mean_shift_z
                or vol_ratio >= self.pair.structural_vol_ratio
            )
            stop_reached = (
                self._position > 0 and liquidation_z <= -self.pair.stop_z
            ) or (
                self._position < 0 and liquidation_z >= self.pair.stop_z
            )
            # 结构突变必须优先于“表面回归”：极端跳变或波动制度变化不能被
            # 有利方向越过均值误标为普通止盈。
            if structural_break:
                action = SignalAction.EMERGENCY_EXIT
                reason = "structural break detected"
            elif reverted:
                action = SignalAction.EXIT
                reason = "executable spread reverted to entry anchor"
            elif stop_reached:
                action = SignalAction.EMERGENCY_EXIT
                reason = "entry anchored executable stop reached"
            elif (
                self.pair.max_holding_samples > 0
                and self._holding_samples >= self.pair.max_holding_samples
            ):
                action = SignalAction.EMERGENCY_EXIT
                reason = "maximum holding period reached"
            if action in {SignalAction.EXIT, SignalAction.EMERGENCY_EXIT}:
                self._position = 0
                self._holding_samples = 0

        self._history.append(mid_spread)
        self._last_sample_ts = timestamp
        return SpreadSignal(
            self.pair.pair_id,
            action,
            chosen_z,
            timestamp,
            chosen_spread,
            mean,
            std,
            reason,
        )

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

    def _mean(self) -> float:
        return sum(self._history) / len(self._history) if self._history else 0.0

    def _std(self, mean: float) -> float:
        if not self._history:
            return 0.0
        variance = sum(
            (value - mean) ** 2 for value in self._history
        ) / len(self._history)
        return sqrt(variance)