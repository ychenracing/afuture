"""
系统配置模块。

集中管理策略、风险和交易环境参数，避免参数散落在代码中。
"""


DEFAULT_CONFIG = {
    "initial_capital": 500000,
    "max_margin_ratio": 0.5,
    "commission_rate": 0.0001,
    "slippage": 1,
}
