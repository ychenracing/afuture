"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import time

from .broker.ctp import CtpBroker
from .config import load_config
from .engine import TradingEngine
from .journal import AuditJournal
from .logging_utils import configure_logging
from .models import AccountSnapshot, ContractPosition, PairConfig
from .replay import run_replay
from .report import write_account_report
from .risk import RiskManager
from .state import StateStore


_LIVE_ACK = "I_UNDERSTAND_FUTURES_RISK"
_RECOVERY_ACK = "I_VERIFIED_CTP_POSITIONS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afuture", description="国内期货跨期套利交易系统")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="校验配置，不连接柜台")
    validate.add_argument("--config", required=True)

    replay = sub.add_parser("replay", help="历史 Tick 回放")
    replay.add_argument("--config", required=True)
    replay.add_argument("--data", required=True)

    live = sub.add_parser("live", help="连接 CTP 柜台")
    live.add_argument("--config", required=True)
    live.add_argument("--confirm-live", action="store_true")
    live.add_argument("--startup-timeout", type=float, default=60.0)
    live.add_argument("--snapshot-wait", type=float, default=12.0)
    live.add_argument("--halt-drain", type=float, default=3.0)

    recover = sub.add_parser("recover-state", help="人工核验后重建本地期望持仓")
    recover.add_argument("--config", required=True)
    recover.add_argument("--confirm-live", action="store_true")
    recover.add_argument("--confirm-adopt-state", action="store_true")
    recover.add_argument("--startup-timeout", type=float, default=60.0)
    recover.add_argument("--snapshot-wait", type=float, default=12.0)
    return parser


def wait_for_fresh_snapshot(
    broker: CtpBroker,
    timeout_seconds: float,
    *,
    poll_interval: float = 0.1,
) -> None:
    """等待 marker 之后的新账户事件和完整持仓快照同时到达。"""
    marker = broker.snapshot_marker()
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while not broker.snapshot_ready(marker) and time.monotonic() < deadline:
        time.sleep(max(poll_interval, 0.0001))
    if not broker.snapshot_ready(marker):
        raise RuntimeError("fresh CTP account/position snapshot did not arrive before timeout")



def drain_after_halt(
    engine: TradingEngine,
    broker,
    timeout_seconds: float,
    *,
    poll_interval: float = 0.1,
) -> bool:
    """停机后持续处理撤单/成交回报，尽量在断开前清空活动委托。"""
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        active_orders = broker.get_active_orders()
        if not active_orders:
            return True
        for order in active_orders:
            broker.cancel_order(order.order_id)
        if time.monotonic() >= deadline:
            return False
        engine.run_once()
        time.sleep(max(poll_interval, 0.0001))


def validate_recovery_positions(
    pairs: list[PairConfig], positions: list[ContractPosition]
) -> None:
    """恢复时只接受配置内、数量精确且双腿方向相反的完整套利持仓。"""
    allowed_symbols = {
        symbol
        for pair in pairs
        for symbol in (pair.near_symbol, pair.far_symbol)
    }
    unknown = sorted(
        position.symbol for position in positions if position.symbol not in allowed_symbols
    )
    if unknown:
        raise RuntimeError(
            f"broker positions are not configured for afuture: {', '.join(unknown)}"
        )

    by_symbol = {position.symbol: position for position in positions if not position.empty}
    for pair in pairs:
        near = by_symbol.get(
            pair.near_symbol, ContractPosition(pair.near_symbol, pair.exchange)
        )
        far = by_symbol.get(
            pair.far_symbol, ContractPosition(pair.far_symbol, pair.exchange)
        )
        if near.empty and far.empty:
            continue
        if near.exchange != pair.exchange or far.exchange != pair.exchange:
            raise RuntimeError(
                f"pair {pair.pair_id} exchange does not match configured exchange"
            )
        long_spread = (
            near.long_total == far.short_total == pair.volume
            and near.short_total == 0
            and far.long_total == 0
        )
        short_spread = (
            near.short_total == far.long_total == pair.volume
            and near.long_total == 0
            and far.short_total == 0
        )
        if not (long_spread or short_spread):
            raise RuntimeError(
                f"pair {pair.pair_id} is not a configured balanced spread"
            )


def adopt_recovery_state(
    store: StateStore,
    state,
    account: AccountSnapshot,
    positions: list[ContractPosition],
) -> None:
    """人工恢复只重建期望持仓，不解除停机；下一次启动仍需独立再次对账。"""
    old_day = str(state.trading_day or "")
    new_day = str(account.trading_day or "")
    if new_day and new_day != old_day:
        state.day_start_equity = account.equity
    elif state.day_start_equity <= 0:
        state.day_start_equity = account.equity
    if new_day:
        state.trading_day = new_day
    state.equity_high_watermark = max(
        float(state.equity_high_watermark or 0.0), account.equity
    )
    state.kill_switch = True
    state.kill_reason = (
        "operator adopted verified CTP positions; restart live for independent reconciliation"
    )
    state.reconciled = False
    store.save_positions(state, positions)


def _require_production_confirmation(config, args) -> None:
    if config.ctp is None or config.ctp.environment != "production":
        return
    if not args.confirm_live or os.getenv("AFUTURE_LIVE_ACK") != _LIVE_ACK:
        raise RuntimeError(
            "production live trading requires --confirm-live and "
            "AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK"
        )


def _wait_until_ready(broker: CtpBroker, timeout_seconds: float) -> None:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while not broker.is_ready() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not broker.is_ready():
        raise RuntimeError("CTP did not become ready before timeout")


def _recover_state(config, args, logger) -> int:
    """在持久化停机状态下，经过强确认后把人工核验的柜台持仓重新锚定为期望状态。"""
    if config.mode != "live" or config.ctp is None:
        raise ValueError("recover-state requires system.mode=live")
    _require_production_confirmation(config, args)
    if (
        not args.confirm_adopt_state
        or os.getenv("AFUTURE_RECOVERY_ACK") != _RECOVERY_ACK
    ):
        raise RuntimeError(
            "state recovery requires --confirm-adopt-state and "
            "AFUTURE_RECOVERY_ACK=I_VERIFIED_CTP_POSITIONS"
        )

    store = StateStore(config.state_path)
    state = store.load()
    if not state.kill_switch:
        raise RuntimeError("state recovery is allowed only while the kill switch is active")

    broker = CtpBroker(config.ctp)
    broker.start()
    try:
        _wait_until_ready(broker, args.startup_timeout)
        wait_for_fresh_snapshot(broker, args.snapshot_wait)
        active_orders = broker.get_active_orders()
        if active_orders:
            for order in active_orders:
                broker.cancel_order(order.order_id)
            state.kill_switch = True
            state.reconciled = False
            state.kill_reason = (
                "active orders found during state recovery; cancelled; rerun recovery after verification"
            )
            store.save(state)
            raise RuntimeError(
                "active orders existed during recovery and were cancelled; state was not adopted"
            )

        account = broker.get_account()
        positions = broker.get_positions()
        validate_recovery_positions(config.pairs, positions)
        adopt_recovery_state(store, state, account, positions)
        AuditJournal(config.journal_path).record(
            "manual_state_adoption",
            {
                "trading_day": account.trading_day,
                "positions": positions,
                "kill_switch_remains_active": True,
            },
        )
        write_account_report(config.report_path, account, positions)
        logger.warning(
            "已重建本地期望持仓，但停机开关仍保持；必须重新执行 live 完成第二次独立对账"
        )
        return 0
    finally:
        broker.stop()

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logger = configure_logging(config.log_path)

    if args.command == "validate":
        logger.info("配置校验通过")
        return 0
    if args.command == "replay":
        account = run_replay(config, args.data)
        logger.info("回放完成：权益 %.2f，保证金 %.2f", account.equity, account.margin)
        return 0

    if args.command == "recover-state":
        return _recover_state(config, args, logger)

    if config.mode != "live" or config.ctp is None:
        raise ValueError("live command requires system.mode=live")
    _require_production_confirmation(config, args)

    broker = CtpBroker(config.ctp)
    engine = TradingEngine(
        broker,
        config.pairs,
        config.contracts,
        RiskManager(config.risk),
        StateStore(config.state_path),
        auto_flatten_imbalance=config.auto_flatten_imbalance,
        aggressive_ticks=config.aggressive_ticks,
        legging_timeout_seconds=config.legging_timeout_seconds,
        journal=AuditJournal(config.journal_path),
    )
    engine.start()
    try:
        try:
            _wait_until_ready(broker, args.startup_timeout)
        except RuntimeError:
            engine.emergency_stop("CTP startup timeout")
            raise

        # 等待官方定时查询链路在“就绪之后”重新返回账户和完整持仓快照。
        # 不并发手工触发查询，避免和 CTP 查询流控产生竞争。
        try:
            wait_for_fresh_snapshot(broker, args.snapshot_wait)
        except RuntimeError:
            engine.emergency_stop("CTP fresh account/position snapshot timeout")
            raise
        engine.initialize_after_ready()

        # 首次启动或重启都先核对持仓；已有活动订单视为不安全启动。
        active_orders = broker.get_active_orders()
        if active_orders:
            for order in active_orders:
                broker.cancel_order(order.order_id)
            engine.emergency_stop("active orders found during startup reconciliation")
            raise RuntimeError("active orders existed at startup and were cancelled")

        if engine.halted:
            if not engine.clear_kill_switch_after_reconcile():
                raise RuntimeError("kill switch remains active because reconciliation did not pass")
        elif not engine.reconcile_startup():
            raise RuntimeError("startup reconciliation failed")

        logger.info("CTP 已就绪并完成持仓对账，交易循环启动")
        while True:
            engine.run_once()
            if engine.halted:
                raise RuntimeError(f"trading halted: {engine.state.kill_reason}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("收到人工停止请求")
    finally:
        if engine.halted and broker.is_ready():
            try:
                if not drain_after_halt(engine, broker, args.halt_drain):
                    logger.error("停机后仍有活动委托；已保留停机开关，下一次启动会再次检查并撤单")
            except Exception as exc:
                logger.error("停机撤单收尾失败：%s", exc)
        try:
            write_account_report(config.report_path, broker.get_account(), broker.get_positions())
        except Exception as exc:
            logger.error("关闭前账户报告写入失败：%s", exc)
        engine.stop()
    return 0
