# 数据、回放与研究

## CSV 格式

必需列：

```text
timestamp,symbol,exchange,bid_price,ask_price,last_price,bid_volume,ask_volume,trading_day
```

建议同时提供：

```text
limit_up,limit_down,volume,open_interest
```

约束：

- `timestamp` 必须带时区；
- `trading_day` 使用期货交易日，不是简单自然日；
- bid/ask 和一档数量必须有效；
- `volume`、`open_interest` 不得为负；
- 跨期两腿应尽量同步采样。

回放会把同一时间戳的两腿先一起注入模拟柜台，再执行事件循环，避免分钟级历史数据因文件顺序产生虚假的跨腿 stale-quote 停机。 回放健康监控使用事件时间；与之不同，实盘健康监控使用墙钟，因此即使所有 Tick 同时停止推送也能触发陈旧行情停机。

## 普通与保守撮合

`SimBroker` 支持：

- 限价、FAK、FOK；
- 一档盘口部分成交；
- 同一 Tick 内共享并消耗可用深度；
- 手续费；
- 滑点；
- 今昨仓和平仓规则。

保守模式额外支持：

- `latency_ticks`：订单要等若干新 Tick 后才具备成交资格；
- `market_impact_ticks`：成交价进一步向不利方向移动；
- FAK/FOK 在第一个可执行 Tick 后立即取消未成交余量。

它仍然不能完整模拟真实交易所队列位置、网络抖动、多档盘口和极端行情，因此应把回放结果看成筛选证据，而不是收益保证。

## Scanner

`afuture scan` 对每个配置组合计算：

- 当前 Z-score；
- 一档流动性分数；
- 成交量分数；
- Open Interest 分数；
- 均值回归半衰期代理；
- 平稳性代理；
- 当前方向的 Net Edge；
- 综合候选分数。

命令行 `scan` 本身不会开仓；实盘 `auto` 模式会复用同一评分逻辑，但候选仍必须再进入 `TradingEngine → RiskManager → PairExecutor` 的正式权限链，Scanner 自己没有下单权限。


## Auto 回放

`replay` 也可以使用 `auto.enabled = true`，从而验证“自动选哪个组合”而不只验证单一固定 pair。历史配置中的 `[[contracts]]` 需要额外提供：

```toml
product = "m"
expiry = "2026-09-15"
```

模拟柜台会把这些字段作为历史合约目录暴露给 `AutoPairManager`，随后使用与实盘相同的：

- 到期过滤；
- 相邻月份生成；
- 成交量/Open Interest/盘口过滤；
- Z-score、半衰期、平稳性和 Net Edge 排名；
- 激活/保护已有仓位/平仓后退役。

这样自动选择策略可以先在多品种历史数据上做 OOS 回放，再进入 CTP 测试柜台。实盘的合约目录仍只信任 CTP，不读取研究配置中的 expiry。

## Walk-forward

`afuture accept` 使用严格的时间顺序：

```text
Train → Validation → OOS
```

参数仅由 Train + Validation 选择，OOS 不反向参与参数选择。多组候选参数必须形成稳定邻域；如果只有互相远离的孤立峰值，Calibrator 可以拒绝选出参数。

随后对最终候选执行交易成本倍增压力，例如：

```text
1.0x
1.5x
2.0x
```

压力倍数会同时放大手续费、滑点和裸腿风险缓冲。

## 主要指标

回放/研究输出至少包括：

- 总收益；
- 最终权益；
- 最大回撤；
- 日频 Sharpe；
- 交易次数；
- OOS 正收益比例；
- 成本压力结果。

短样本 Sharpe 和年化收益非常容易失真。正式研究应优先看多窗口稳定性、回撤和成本压力，而不是追求单个窗口最高收益。
