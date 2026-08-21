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

同一 timestamp 的多腿行情先作为一批注入模拟柜台，再运行一次事件循环。时间语义必须与数据本身一致：

- 历史行情健康与裸腿超时使用市场事件时间，不能让几个月回放被 CPU 几秒压缩；
- 普通报单限速使用已经通过行情检查的 `signal.timestamp`，因此跨交易日历史报单不会被错误当成“同一分钟”；
- 实盘仍由墙钟健康检查负责发现陈旧行情、断线和长时间无更新；
- 无论研究还是实盘，事件时间倒退都不会被当作新的可用 Alpha。

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

通用 `accept-auto` 默认不优化：

- risk budget；
- margin cap；
- max active pairs；
- 每个商品独立参数；
- 生产时在线参数。

这样优先控制自由度，而不是追求历史最高收益。

本仓库的专用两年真实数据研究脚本是一个**离线、受硬上限约束的额外研究流程**：它在信号、机会质量、Regime/Carry 都冻结后，才允许搜索有限的 `risk_budget_ratio` 和 `max_pair_volume`，且分别受代码硬上限约束。该流程不意味着生产会在线自动放大杠杆。

## 6. Regime / Carry 候选能力

为避免均值回归在关系失效、波动突变或单边曲线行情中机械逆势开仓，Auto Selector 支持以下轻量候选证据：

- 多个滚动子窗口的均值回归持久性；
- 当前短期波动率相对历史的分位；
- 短期水平相对慢速历史基准的 change-point / trend-shift 代理；
- `log(near/far)` 归一化曲线 carry reversal；
- carry 软排名只奖励与实际 LONG/SHORT spread 方向一致的证据。

这些思想参考了公开的 pairs-trading、regime/change-point、commodity term-structure 研究，但 afuture 不直接复制第三方回测结论，也不把 EGARCH、GP、TensorFlow 等重依赖引入生产链。所有门默认可以保持零影响；只有 afuture 自己的开发集滚动 OOS、成本压力和消融给出增量证据时，才有资格收紧生产默认。

参考项目的历史收益**不是** afuture 的收益证据。

## 7. 最终 Auto Portfolio Stress

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

## 8. 预注册晋级门

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

## 9. 两年真实日线研究的边界

`tools/run_real_two_year_research.py` 固定使用 2024-08-21 至 2026-08-20 的合约日线研究窗口，并保留约最后 120 个交易日作为参数冻结后的基准区间。当前实现遵守：

- 当日决策价格只使用当日 `open`；
- volume / open interest 只使用前一交易日已完成数据；
- 当日 high / low / close 不进入当日选标和成交；
- 合约在历史时点必须已经出现真实行情，不能把未来才有数据的远月提前放进 Universe；
- 参数搜索顺序为 Signal → Opportunity Quality → Regime/Carry → bounded Risk Scaling；
- Regime/Carry 只做单轴小邻域，不做五维笛卡尔积；
- 参数冻结后才跑最后基准区间、2x 成本压力和 leave-one-product-out；
- 窗口结束时必须用最后已存在的行情真实结清；没有可用报价或需要未来报价时研究失败关闭。

但必须明确：Sina 日线没有历史一档 bid/ask/depth。研究中的 bid/ask、depth、slippage 只能是保守执行代理，因此这套两年结果主要回答“日频跨期 Alpha 和组合行为是否值得继续”，**不能**证明真实 CTP 盘口中一定能获得相同收益。

此外，该最后基准区间已经被前一代策略研究观察过，因此在新增 Regime/Carry 能力后只能称为“锁定复验区间”，不能包装成全新的 pristine holdout。真正新的生产证据必须继续来自后续未见数据、CTP Shadow 和极小真实仓位。

## 10. 推荐正式数据范围

更高等级的正式证据应由外部历史 L1/Tick 数据提供标准 CSV，至少覆盖约两年并包含：

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

## 11. 研究结果的正确阅读方式

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