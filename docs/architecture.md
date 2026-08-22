# 架构与数据流

## 1. 总体原则

`afuture` 只保留一套账户/订单/成交/持仓真相和一套统一风险治理，但现在支持两种**账户互斥**的策略模式：

- `calendar / auto`：同品种相邻月份跨期套利；
- `directional`：冻结的 50 品种 execution-aligned 方向组合。

配置加载阶段禁止 directional 与 static pairs/Auto 同时启用。两种模式最终都由 Broker 提供账户真相，由 `RiskManager`、TradingEngine 状态机、Kill Switch、Shadow 和审计边界治理。

## 2. Calendar Spread / Auto 数据流

```text
CTP Catalog / Tick
        ↓
AutoPairManager
expiry → front-3 → adjacent months
        ↓
activity / sync / stationarity / half-life / Net Edge
        ↓
CalendarSpreadStrategy
        ↓
PortfolioRisk + RiskManager
        ↓
PairExecutor
        ↓
SimBroker / ShadowBroker / CtpBroker
```

`AutoPairManager` 只决定哪些同品种相邻月份组合具有开仓资格，不直接发订单。已有仓位失去资格时继续保持 managed，但立即失去 open-eligible 权限，直到退出后退役。

## 3. Execution-Aligned Directional 数据流

```text
Sina/AKShare 连续日线 OHLC
        ↓
ExecutionAlignedAggressivePolicy
冻结 96-template pool
        ↓
前一收盘信号 + 已完成 intraday proxy meta score
        ↓
目标产品权重，gross <= 2.0x
        ↓
CTP Catalog + 实时 Tick
        ↓
DirectionalContractSelector
point-in-time OI/volume + 20 天 expiry filter
        ↓
build_target_lots / build_rebalance_plan
        ↓
先减仓，再允许开仓
        ↓
RiskManager single-contract + batch account gates
        ↓
FAK → CtpBroker
```

### 冻结经济参数

生产 copy 固定：

- 50 个品种 Universe，按代码字母序规范化；
- specific-contract 历史选择后的 96 个模板；
- family：breakout / tsmom / momentum / moving-average / reversal / acceleration；
- meta lookback = 10；
- meta rebalance = 5；
- meta count = 3；
- meta score source = 已完成的连续合约 `open→close` 日内代理；
- gross target ≤ 2.0x。

生产代码不在运行时重新做历史参数搜索。

## 4. 因果时间边界

Directional 的目标权重遵守：

```text
截至 t 日收盘可见数据
        ↓
计算 t+1 目标产品权重
        ↓
下一可交易时段使用当前 CTP 合约/盘口执行
```

历史 L4 对执行做更严格的分解：旧权重承担 `previous close → next open` gap，新目标权重承担 `next open → close`；换月收益始终来自上一决策日已选择的同一具体合约。

Final OOS 已被多轮观察，统一标记 `pristine_final_oos=false`。

## 5. 合约选择边界

### Calendar Spread

- CTP/历史 point-in-time catalog；
- 到期过滤；
- front-3；
- 相邻月份；
- 双腿同步盘口和活动度。

### Directional

- 配置只允许冻结的 50 品种；
- 从 CTP catalog 中保留已挂牌、允许交易所、未进入交割黑窗的合约；
- 使用当前实时 Tick 的 Open Interest、成交量选择具体合约；
- 没有 fresh/eligible contract 时 fail closed，不为该次 rebalance 新增风险。

L4 的 specific-contract 数据也使用 point-in-time OI/volume 和 20 天黑窗，避免连续合约换月跳空伪造收益。

## 6. 风险权限

`RiskManager` 仍是新增风险最终权限拥有者。

Calendar pair 额外有：

- `max_open_pairs`；
- 双腿同步；
- 双腿 depth；
- pair calendar/session；
- PairExecutor Net Edge。

Directional 单合约新增风险依次经过：

1. fresh quote；
2. session；
3. bid/ask width；
4. top-of-book depth；
5. limit-distance；
6. 单合约手数上限；
7. 账户日亏损/总回撤；
8. 合计保证金率；
9. 最小可用资金率；
10. 报单频率。

2.0x gross 只是策略目标上限，不会放宽任何账户风险门。

## 7. 减仓优先与状态机

Directional 调仓不会在同一阶段一边平旧合约一边加新风险：

```text
存在 reductions
    ↓
只发送 reducing FAK
    ↓
等待 Broker 确认后的下一周期
    ↓
无 reductions 才允许 openings
```

TradingEngine 继续维护：

```text
RUNNING
  ├─ 严重异常 → HALTED
  └─ 需要退出的风险 → REDUCE_ONLY → HALTED
```

`DirectionalTradingEngine` 只扩展策略生命周期；状态文件、Kill Switch、账户检查、对账和 Broker 事件处理仍复用基础 `TradingEngine`。

## 8. Broker 真相

本地策略不自行推进“已成交仓位”。真实账户中的：

- account；
- active order；
- trade；
- position；

全部以 Broker/CTP 回报为准。Directional manager 每次调仓都读取 Broker 当前持仓再计算 delta。

## 9. Shadow

`ShadowBroker` 仍然使用：

```text
真实 CTP：catalog / tick / trading_day / metadata / signal market context
本地 SimBroker：account / order / trade / position
```

Directional Shadow 与 Calendar Shadow 通过同一个 CLI runtime factory 进入对应 account-exclusive engine，但不会发真实 CTP 订单。

## 10. 研究与生产分离

研究脚本可以搜索和比较更多模板；生产 policy 只包含冻结结果。最终高收益证据分三层：

1. broad L3：连续合约发现高收益 family；
2. specific-contract L4：真实换月和 next-open 执行；
3. production policy：把最终冻结公式复制为独立模块，避免后续研究改动静默改变实盘行为。

最终 historical L4：2024-08-21~2026-08-20，5bp 单边，年化约 107.46%、最大回撤约 27.41%、gross ≤2x；但该结果具有显式选择偏差，Final OOS 并未独立通过。

## 11. 不增加的系统层

项目仍不需要数据库、消息队列、Web 服务、微服务或另一套账户状态机。当前优先级是收益来源、真实执行质量和未来新数据，而不是系统体量。