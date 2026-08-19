"""本地状态与柜台真实持仓对账。"""

from dataclasses import dataclass

from .models import ContractPosition


@dataclass(frozen=True)
class ReconcileResult:
    matched: bool
    details: str = ""


def compare_positions(local: list[ContractPosition], remote: list[ContractPosition]) -> ReconcileResult:
    """逐合约比较今昨、多空数量；价格差异不影响能否继续发单。"""
    def normalized(items: list[ContractPosition]) -> dict[str, tuple[int, int, int, int]]:
        return {
            p.symbol: (p.long_today, p.long_yesterday, p.short_today, p.short_yesterday)
            for p in items
            if not p.empty
        }

    left = normalized(local)
    right = normalized(remote)
    if left == right:
        return ReconcileResult(True)
    symbols = sorted(set(left) | set(right))
    diffs = [f"{symbol}: local={left.get(symbol)}, remote={right.get(symbol)}" for symbol in symbols if left.get(symbol) != right.get(symbol)]
    return ReconcileResult(False, "; ".join(diffs))
