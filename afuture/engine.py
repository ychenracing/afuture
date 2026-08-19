"""实时和历史回放共用的套利交易引擎。"""

from __future__ import annotations

from dataclasses import asdict
from time import monotonic

from .execution import PairExecutor
from .models import ContractPosition, ContractSpec, Order, PairConfig, SignalAction, Tick, Trade
from .position import PositionBook
from .reconcile import compare_positions
from .risk import RiskManager
from .state import RuntimeState, StateStore
from .strategy import CalendarSpreadStrategy


class TradingEngine:
    """把行情、策略、风控、执行、对账和持久化串成单一链路。"""

    def __init__(
        self,
        broker,
        pairs: list[PairConfig],
        specs: dict[str, ContractSpec],
        risk_manager: RiskManager,
        state_store: StateStore,
        *,
        auto_flatten_imbalance: bool = True,
        aggressive_ticks: int = 1,
        legging_timeout_seconds: float = 2.0,
        journal=None,
    ) -> None:
        self.broker = broker
        self.pairs = {pair.pair_id: pair for pair in pairs}
        self.specs = specs
        self.risk_manager = risk_manager
        self.state_store = state_store
        self.strategies = {pair.pair_id: CalendarSpreadStrategy(pair) for pair in pairs}
        self.executor = PairExecutor(
            broker,
            risk_manager,
            specs,
            aggressive_ticks=aggressive_ticks,
        )
        self.quotes: dict[str, Tick] = {}
        self.state = RuntimeState()
        self.halted = False
        self.auto_flatten_imbalance = auto_flatten_imbalance
        self.legging_timeout_seconds = max(0.0, legging_timeout_seconds)
        self._imbalance_since: dict[str, float] = {}
        self._initialized = False
        self.journal = journal

    def start(self) -> None:
        """启动柜台和订阅；异步 CTP 尚未登录时不读取账户。"""
        self.state = self.state_store.load()
        self.risk_manager.restore_high_watermark(self.state.equity_high_watermark)
        self.halted = self.state.kill_switch
        for pair_id, strategy in self.strategies.items():
            saved = self.state.strategy_states.get(pair_id)
            if saved:
                strategy.restore_state(saved)
        self.broker.start()
        for pair in self.pairs.values():
            self.broker.subscribe(pair.near_symbol, pair.exchange)
            self.broker.subscribe(pair.far_symbol, pair.exchange)
        if self.broker.is_ready():
            self.initialize_after_ready()

    def initialize_after_ready(self) -> None:
        """柜台登录并取得账户后建立风险基线；持仓状态必须等对账通过后再同步。"""
        if not self.broker.is_ready():
            raise RuntimeError("broker is not ready")
        account = self.broker.get_account()
        self._advance_trading_day(account)
        self.risk_manager.set_day_start_equity(
            self.state.day_start_equity, self.state.trading_day
        )
        self.risk_manager.restore_high_watermark(self.state.equity_high_watermark)
        decision = self.risk_manager.check_account(account)
        self.state.equity_high_watermark = self.risk_manager.high_watermark
        self._initialized = True
        if not decision.allowed:
            self.emergency_stop(decision.reason)
            return
        self._persist_strategy_states()

    def stop(self) -> None:
        """保存本系统的期望状态后关闭柜台，不用远端快照覆盖本地真相。"""
        self._persist_strategy_states()
        self.broker.stop()

    def reconcile_startup(self) -> bool:
        """重启后必须和柜台完整持仓一致，才能继续交易或解除停机。"""
        remote = self.broker.get_positions()
        local = self.state_store.positions_from_state(self.state)
        result = compare_positions(local, remote)
        self.state.reconciled = result.matched
        if not result.matched:
            self.emergency_stop(f"position reconciliation failed: {result.details}")
            return False

        # 数量一致后才允许用柜台快照刷新均价等非方向字段。
        self.state.positions = [asdict(position) for position in remote]
        self._sync_strategy_positions(remote)
        self._persist_strategy_states()
        return True

    def clear_kill_switch_after_reconcile(self) -> bool:
        """停机状态只能在柜台就绪且完整对账通过后清除。"""
        if not self.broker.is_ready():
            return False
        if not self._initialized:
            self.initialize_after_ready()
        if not self.reconcile_startup():
            return False
        if not self.state_store.can_clear_kill_switch(self.state):
            return False
        decision = self.risk_manager.check_account(self.broker.get_account())
        if not decision.allowed:
            self.emergency_stop(decision.reason)
            return False
        self.state.kill_switch = False
        self.state.kill_reason = ""
        self.halted = False
        self.state_store.save(self.state)
        return True

    def on_tick(self, tick: Tick) -> None:
        try:
            tick.validate()
            self.quotes[tick.symbol] = tick
            if self.halted:
                return
            # 策略持仓必须由已确认成交形成的期望持仓驱动，不能把“报单已接受”当成“已成交”。
            self._sync_strategy_positions()
            account_decision = self.risk_manager.check_account(self.broker.get_account())
            if not account_decision.allowed:
                self.emergency_stop(account_decision.reason)
                return
            for pair_id, pair in self.pairs.items():
                if tick.symbol not in {pair.near_symbol, pair.far_symbol}:
                    continue
                if self._pair_has_active_orders(pair_id):
                    continue
                near = self.quotes.get(pair.near_symbol)
                far = self.quotes.get(pair.far_symbol)
                if near is None or far is None:
                    continue
                quote_time = max(near.timestamp, far.timestamp)
                quote_decision = self.risk_manager.check_quotes([near, far], quote_time)
                if not quote_decision.allowed:
                    continue
                signal = self.strategies[pair_id].on_quotes(near, far)
                if signal.action is SignalAction.HOLD:
                    continue
                self._record("signal", signal)
                result = self.executor.execute_signal(
                    pair,
                    signal,
                    near,
                    far,
                    open_pair_count=self._open_pair_count(),
                )
                if not result.accepted and signal.action in {
                    SignalAction.LONG_SPREAD,
                    SignalAction.SHORT_SPREAD,
                }:
                    self.strategies[pair_id].set_position(0)
                self._persist_strategy_states()
                if signal.action is SignalAction.EMERGENCY_EXIT and not result.accepted:
                    self.emergency_stop(f"emergency exit failed: {result.reason}")
        except Exception as exc:
            self.emergency_stop(f"engine exception: {exc}")

    def run_once(self) -> None:
        """处理柜台异步事件，外层循环可按约 50~200 毫秒调用。"""
        if not self.broker.is_ready():
            if self._initialized and not self.halted:
                self.emergency_stop("broker session disconnected")
            return
        if not self._initialized:
            self.initialize_after_ready()
        health_error = getattr(self.broker, "health_error", lambda: None)()
        if health_error and not self.halted:
            self.emergency_stop(health_error)
            return

        for event in self.broker.poll_events():
            if event.event_type == "tick":
                self.on_tick(event.payload)
                continue

            if event.event_type == "trade":
                trade = event.payload
                self._record("trade", trade)
                if not isinstance(trade, Trade) or not self.broker.owns_order(trade.order_id):
                    self.emergency_stop("unrecognized trade detected; possible external account activity")
                    continue
                try:
                    self._apply_expected_trade(trade)
                except Exception as exc:
                    self.emergency_stop(f"expected position update failed: {exc}")
                    continue
                self._audit_pair_balance()
                continue

            if event.event_type == "position_snapshot":
                self._record("position_snapshot", event.payload)
                self._reconcile_runtime_snapshot(event.payload)
                continue

            if event.event_type == "order":
                order = event.payload
                self._record("order", order)
                if (
                    isinstance(order, Order)
                    and order.active
                    and not self.broker.owns_order(order.order_id)
                ):
                    self.emergency_stop(
                        "unrecognized active order detected; possible external account activity"
                    )
                    continue
                self._audit_pair_balance()
                continue

            if event.event_type == "position":
                # 单笔持仓事件不是完整快照，不能据此覆盖本地期望持仓。
                self._record("position", event.payload)
                self._audit_pair_balance()
                continue

            if event.event_type == "broker_error":
                self.emergency_stop(f"broker error: {event.payload}")
                continue

            if event.event_type == "account":
                self._advance_trading_day(event.payload)
                decision = self.risk_manager.check_account(event.payload)
                self.state.equity_high_watermark = self.risk_manager.high_watermark
                self.state_store.save(self.state)
                if not decision.allowed:
                    self.emergency_stop(decision.reason)
        self._audit_pair_balance()

    def emergency_stop(self, reason: str) -> None:
        """取消活动委托并持久化停机；绝不把异常远端持仓反写为期望持仓。"""
        self._record("emergency_stop", {"reason": reason})
        try:
            for order in self.broker.get_active_orders():
                self.broker.cancel_order(order.order_id)
        finally:
            self.halted = True
            self.state.kill_switch = True
            self.state.kill_reason = reason
            self.state.reconciled = False
            self._persist_strategy_states()

    def _record(self, event_type: str, payload: object) -> None:
        if self.journal is not None:
            self.journal.record(event_type, payload)

    def _apply_expected_trade(self, trade: Trade) -> None:
        """只有本进程已知订单的成交才能推进本地期望持仓。"""
        book = PositionBook(self.state_store.positions_from_state(self.state))
        book.apply_trade(trade)
        self.state.positions = [asdict(position) for position in book.all()]
        self._sync_strategy_positions(book.all())
        self._persist_strategy_states()

    def _reconcile_runtime_snapshot(self, remote: object) -> None:
        """完整柜台快照和本地期望持仓不一致时立即停机。"""
        if self.halted:
            return
        if not isinstance(remote, list) or any(
            not isinstance(position, ContractPosition) for position in remote
        ):
            self.emergency_stop("invalid position snapshot received from broker")
            return
        local = self.state_store.positions_from_state(self.state)
        result = compare_positions(local, remote)
        if not result.matched:
            self.emergency_stop(f"runtime position drift detected: {result.details}")
            return
        # 对账一致时可以刷新均价等非数量字段。
        self.state.positions = [asdict(position) for position in remote]
        self.state.reconciled = True
        self._sync_strategy_positions(remote)
        self._persist_strategy_states()


    def _pair_has_active_orders(self, pair_id: str) -> bool:
        """组合仍有活动委托时不允许同一组合再次生成新订单。"""
        return any(
            order.request.reference.startswith(pair_id)
            for order in self.broker.get_active_orders()
        )

    def _audit_pair_balance(self) -> None:
        if self.halted:
            return
        now = monotonic()
        for pair in self.pairs.values():
            if self.executor.pair_is_balanced(pair):
                self._imbalance_since.pop(pair.pair_id, None)
                continue
            first_seen = self._imbalance_since.setdefault(pair.pair_id, now)
            if now - first_seen < self.legging_timeout_seconds:
                continue
            near = self.quotes.get(pair.near_symbol)
            far = self.quotes.get(pair.far_symbol)
            reason = f"pair imbalance detected: {pair.pair_id}"
            if self.auto_flatten_imbalance and near is not None and far is not None:
                try:
                    self.executor.flatten_imbalance(pair, near, far)
                except Exception as exc:
                    reason += f"; flatten failed: {exc}"
            self.emergency_stop(reason)
            return

    def _open_pair_count(self) -> int:
        positions = {position.symbol: position for position in self.broker.get_positions()}
        count = 0
        for pair in self.pairs.values():
            near = positions.get(pair.near_symbol)
            far = positions.get(pair.far_symbol)
            if near and far and not near.empty and not far.empty:
                count += 1
        return count

    def _sync_strategy_positions(
        self, positions: list[ContractPosition] | None = None
    ) -> None:
        """以已对账的期望持仓为策略状态真相，防止重启后重复开仓。"""
        if positions is None:
            positions = self.state_store.positions_from_state(self.state)
        book = PositionBook(positions)
        for pair_id, pair in self.pairs.items():
            near = book.get(pair.near_symbol, pair.exchange)
            far = book.get(pair.far_symbol, pair.exchange)
            if near.empty and far.empty:
                position = 0
            elif (
                near.long_total == far.short_total > 0
                and near.short_total == 0
                and far.long_total == 0
            ):
                position = 1
            elif (
                near.short_total == far.long_total > 0
                and near.long_total == 0
                and far.short_total == 0
            ):
                position = -1
            else:
                continue
            self.strategies[pair_id].set_position(position)


    def _advance_trading_day(self, account) -> None:
        """交易日变化时先把本地期望今仓滚为昨仓，再更新风险日基线。"""
        new_day = str(account.trading_day or "")
        old_day = str(self.state.trading_day or "")
        if new_day and old_day and new_day != old_day:
            book = PositionBook(self.state_store.positions_from_state(self.state))
            book.roll_trading_day()
            self.state.positions = [asdict(position) for position in book.all()]
            self._sync_strategy_positions(book.all())
        if self.state.day_start_equity <= 0 or (new_day and new_day != old_day):
            self.state.day_start_equity = account.equity
        if new_day:
            self.state.trading_day = new_day
        if self.state.day_start_equity > 0 and self.state.trading_day:
            self.risk_manager.set_day_start_equity(
                self.state.day_start_equity, self.state.trading_day
            )

    def _persist_strategy_states(self) -> None:
        self.state.strategy_states = {
            pair_id: strategy.snapshot_state()
            for pair_id, strategy in self.strategies.items()
        }
        try:
            account = self.broker.get_account()
            self._advance_trading_day(account)
            self.risk_manager.check_account(account)
            self.state.equity_high_watermark = self.risk_manager.high_watermark
        except Exception:
            pass
        self.state_store.save(self.state)
