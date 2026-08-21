# 架构与数据流

## 设计目标

`afuture` 的核心不是“能发 CTP 订单”，而是让策略、风险、执行和真实账户状态之间只有一条明确的数据链。任何模块失败时优先停止新增风险，而不是猜测状态。

## 模块边界

```text
Market Data / Replay CSV
          │
          ▼
     TradingEngine
          │
   ┌──────┼──────────┐
   ▼      ▼          ▼
Strategy  Health   PortfolioRisk
   │                  │
   └──────┬───────────┘
          ▼
     RiskManager
          │
          ▼
     PairExecutor
          │
    ┌─────┴─────┐
    ▼           ▼
 SimBroker    CtpBroker
```

- `CalendarSpreadStrategy`：只产生交易意图，不访问账户。
- `RiskManager`：决定是否允许开仓以及最大动态手数。
- `PortfolioRiskAnalyzer`：从价差变化序列计算相关性，并约束风险组集中度。
- `PairExecutor`：把意图转成双腿订单，负责 Net Edge、盘口、保证金预检、回滚和减仓修复。
- `TradingEngine`：负责事件顺序、状态机、对账、健康门和持久化。 实盘健康门以墙钟检测行情整体冻结；回放健康门以历史事件时间计算跨腿陈旧度。
- `Broker`：隔离模拟撮合与 CTP SDK。

## 可成交价差

策略历史中心仍可使用中间价构造稳定的统计序列，但开仓条件使用方向性可成交价差：

```text
LONG_SPREAD  = near.ask - far.bid
SHORT_SPREAD = near.bid - far.ask
```

执行器再次计算 Net Edge，因此策略信号本身不能绕过交易成本门。

## 状态真相

系统维护两个不同概念：

1. **本地期望持仓**：只由本进程已经确认的成交推进。
2. **柜台完整持仓快照**：只用于对账，不会在异常时直接覆盖本地期望状态。

这样可以避免外部人工成交被系统“自动接纳”为正常状态。运行中如果完整快照和本地期望持仓不一致，会持久化停机。

## 交易日切换

CTP 交易日变化时，本地期望今仓先滚为昨仓，然后再和柜台完整快照对账。这样夜盘跨自然日和第二天重启不会因为今昨仓标签变化产生假漂移。

## 异常状态机

- `RUNNING`：允许经过全部安全门的新开仓和平仓。
- `REDUCE_ONLY`：只允许撤单和减仓；典型触发条件是双腿失衡或紧急退出未完整成交。
- `HALTED`：停止自动交易，要求人工复核。

Kill Switch 持久化在状态文件中；重启不能自动绕过。

## 状态完整性

状态 JSON 使用 envelope：

```text
schema_version
sequence
state
checksum
```

`checksum` 是 canonical JSON 的 SHA-256。程序还保存最后订单 ID 和成交 ID，用于人工审计和未来重复事件保护扩展。旧版裸状态文件可以迁移；高于当前 schema 的未来状态会被拒绝读取。

## CTP 元数据

实盘启动时从 VeighNa 合约缓存读取乘数和最小变动价位，并通过 CTP 查询账户保证金率和手续费。比较规则：

- 交易所、乘数、price tick 必须一致；
- 本地保证金率不得小于实时查询值；
- 本地任一手续费项不得小于实时查询值。

查询失败或无法可靠表达的固定金额保证金结构会 fail-closed。
