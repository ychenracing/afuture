"""期货手续费计算。"""

from .models import ContractSpec, Offset


def calculate_commission(spec: ContractSpec, offset: Offset, price: float, volume: int) -> float:
    """按合约配置计算手续费，不在代码中假设交易所费率长期不变。"""
    turnover = price * spec.multiplier * volume
    fee = spec.fee
    if offset is Offset.OPEN:
        return fee.open_fixed * volume + fee.open_rate * turnover
    if offset is Offset.CLOSE_TODAY:
        return fee.close_today_fixed * volume + fee.close_today_rate * turnover
    return fee.close_fixed * volume + fee.close_rate * turnover
