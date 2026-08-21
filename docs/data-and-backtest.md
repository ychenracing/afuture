# 数据、回放与研究

## 1. 数据格式

CSV 必需列：

```text
timestamp,symbol,exchange,bid_price,ask_price,last_price,bid_volume,ask_volume,trading_day
```

正式 Auto 研究强烈要求：

```text
limit_up,limit_down,volume,open_interest
```

要求：

- `timestamp` 必须带时区；
- `trading_day` 使用期货交易日；
- bid/ask 和一档数量必须有效；
- volume/OI 不得为负；
- 同一 Auto Universe 需要同时覆盖多个品种、多个交割月份和换月周期。

## 2. 先做 Data Quality Gate

```bash
afuture data-check \
  --config config/afuture.auto-replay.example.toml \
  --data path/to/data.csv
```

该命令**不先排序源文件**，因此能够发现数据供应链中的真实乱序。输出包括：

- tick 总数；
- 合约数；
- 交易日数；
- 每日样本数；
- duplicate；
- out-of-order；
- invalid quote；
- volume/OI 缺失数量和比例；
- 涨跌停字段缺失；
- 日内长断档；
- 每品种合约/交易日/样本覆盖；
- 单一品种最大样本占比；
- 每个交易日根据**当天实际出现的合约**能生成多少 Auto pair。

一个历史目录里存在两个合约并不够；当天数据必须真的同时出现两腿，否则该交易日 Auto candidate 数为 0 并视为硬失败。

## 3. Replay 与真实生产链一致

`afuture replay` 使用：

```text
AutoPairManager
→ TradingEngine
→ CalendarSpreadStrategy
→ PortfolioRisk / RiskManager
→ PairExecutor
→ SimBroker
```

同一 timestamp 的多腿行情先作为一批注入模拟柜台，再运行一次事件循环。历史健康时钟使用事件时间，实盘使用墙钟。

`SimBroker` 支持：

- FAK/FOK/LIMIT；
- 一档深度共享与消耗；
- 部分成交；
- 手续费；
- 滑点；
- latency ticks；
- market impact；
- 今昨仓和平仓规则。

它不能完整模拟交易所队列位置和极端网络条件，因此回放只作为筛选证据。

## 4. 单 Pair 与最终 Auto Portfolio 必须区分

`afuture accept` 用于固定 pair 的研究诊断。它适合：

- 看某个价差是否存在均值回归；
- first divergence；
- 验证具体参数行为。

它**不能**代替最终 Auto 组合晋级，因为最终机器人还有：

- 多品种候选竞争；
- max active pairs；
- 动态 pair 生命周期；
- 动态手数；
- PortfolioRisk；
- 资金竞争；
- 换月。

生产晋级应使用：

```bash
afuture accept-auto ...
```

## 5. `accept-auto` 时间隔离

严格顺序：

```text
Train → Validation → 参数冻结 → OOS
```

OOS 绝不参与参数选择。

默认没有高维网格，而是在当前配置周围构造一个小型**全局参数邻域**：

- lookback；
- entry_z；
- exit_z；
- min_net_edge；
- min_stationarity_score；
- max_half_life。

不优化：

- risk budget；
- margin cap；
- max active pairs；
- 每个商品独立参数；
- 生产时在线参数。

这样优先控制自由度，而不是追求历史最高收益。

## 6. 最终 Auto Portfolio Stress

### 成本

```text
1.0x / 1.5x / 2.0x
```

同时放大手续费/滑点/legging buffer。

### Universe

- leave-one-product-out；
- single-product attribution。

用于检查收益是否其实由单一历史赢家提供。

### 时间

- remove-best-OOS-period。

删除最佳 OOS 段后如果策略严重崩塌，说明结果可能依赖偶然历史窗口。

### 微观结构

- top-depth haircut；
- +1/+2 latency ticks；
- +1 market-impact tick。

### 数据扰动

- 2% / 5% 确定性 Tick gap；
- 0.5 / 1 / 2 秒远月 quote skew；
- 少量 volume/OI missing。

压力场景不是为了准确预测未来，而是检查安全边际。

## 7. 预注册晋级门

默认起点：

| Gate | 默认值 |
|---|---:|
| Aggregate OOS Return | > 0 |
| Positive OOS Fold Ratio | >= 60% |
| Worst OOS Drawdown | <= 6% |
| OOS trade sample | 至少有基本交易样本 |
| Worst cost stress | 不低于 -2% |
| Leave-one-product-out | 不得灾难性崩塌 |
| Single-product concentration | 多品种时受限 |

这些门应该在正式最终 OOS 前确定。不能看见不满意的 OOS 后再反向降低门槛。

## 8. 推荐正式数据范围

代码仓库不能凭空产生真实两年数据。正式证据建议由外部数据源提供标准 CSV，至少覆盖约两年并包含：

- 高/低波动；
- 趋势商品环境；
- 震荡环境；
- 多次换月；
- 不同流动性阶段；
- 多个白名单品种；
- bid/ask/depth/volume/OI。

外部数据进入 afuture 后的固定流程：

```text
data-check
→ accept-auto
→ Shadow
→ CTP test cabinet
→ tiny live
```

不要为了得到更高历史收益在同一 OOS 上反复改白名单和参数。

## 9. 研究结果的正确阅读方式

优先顺序：

1. OOS Calmar / Return；
2. Worst OOS Drawdown；
3. Positive fold ratio；
4. 2x cost stress；
5. Leave-one-product-out；
6. 单品种贡献集中度；
7. 交易次数；
8. 实盘/Shadow execution quality。

单个窗口年化收益或 Sharpe 在样本短时很容易失真，不应该成为唯一目标。
