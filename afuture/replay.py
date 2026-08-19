"""历史 Tick 回放入口。"""

from pathlib import Path

from .broker.sim import SimBroker
from .data import read_ticks
from .engine import TradingEngine
from .journal import AuditJournal
from .report import calculate_performance, write_account_report
from .risk import RiskManager
from .state import StateStore


def run_replay(config, data_path: str | Path) -> object:
    """用与实盘相同的策略、风控和执行链路做确定性回放。"""
    state_path = Path(config.state_path)
    # 回放默认从干净状态开始，防止上一次回放或实盘停机状态污染研究结果。
    if state_path.exists():
        state_path.unlink()

    broker = SimBroker(
        config.initial_capital,
        config.contracts,
        slippage_ticks=config.slippage_ticks,
    )
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
    equity_curve: list[tuple[str, float]] = []
    try:
        for tick in read_ticks(data_path):
            broker.publish_tick(tick)
            # 模拟柜台事件中包含 Tick，统一从事件循环驱动，避免重复采样。
            engine.run_once()
            account = broker.get_account()
            equity_curve.append((tick.trading_day, account.equity))
        account = broker.get_account()
        positions = broker.get_positions()
        metrics = calculate_performance(
            equity_curve,
            initial_capital=config.initial_capital,
            trade_count=len(broker.get_trades()),
        )
        write_account_report(config.report_path, account, positions, metrics)
        return account
    finally:
        engine.stop()
