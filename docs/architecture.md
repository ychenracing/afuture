# 架构与数据流

## 1. 总体原则

`afuture` 支持两种**账户互斥**的正式模式：

- `calendar / auto`：同品种相邻月份跨期套利；
- `directional`：冻结的 50 品种 execution-aligned 方向组合。

两种模式共用唯一的 Broker/TradingEngine 账户真相、`RiskManager`、Kill Switch、`REDUCE_ONLY`、StateStore、Shadow 和审计链。Directional 不创建第二套订单/成交/持仓状态机。

## 2. Calendar / Auto

```text
CTP Catalog / Tick
→ AutoPairManager
→ activity / sync / stationarity / half-life / Net Edge
→ CalendarSpreadStrategy
→ PortfolioRisk + RiskManager
→ PairExecutor
→ Broker
```

Auto 只负责候选和开仓资格；已有仓位失去候选资格后继续 managed，但不再 open-eligible，直至退出。

## 3. Directional

```text
连续 OHLC
→ ExecutionAlignedAggressivePolicy（唯一生产 directional signal policy）
→ 冻结产品权重，gross <=2x

CTP Tick.trading_day
→ DirectionalActivityTracker
→ trading day 切换时冻结前一日最终 OI/volume
→ DirectionalActivityStore（原子 JSON sidecar）
→ D+1 concrete contract selection

Broker positions + D+1 fresh quotes
→ integer target lots
→ reduction-first rebalance
→ RiskManager
→ FAK
→ Broker
```

### 冻结经济参数

- Universe：50 品种，代码字母序；
- template pool：固定 96；
- family：breakout / tsmom / momentum / moving-average / reversal / acceleration；
- meta lookback = 10；
- meta rebalance = 5；
- active templates = 3；
- meta score = 已完成 continuous `open→close` intraday proxy；
- gross target ≤2.0x。

`execution_aligned_policy.py` 自包含最终 signal/meta helper。`directional.py` 只保留配置、合约选择、整数手数和 rebalance 原语，不再保留旧 32-template 中间 policy。

## 4. 因果时间边界

Directional 同时冻结两个“截至何时可见”的证据：

```text
完整交易日 D 的 OHLC
→ D+1 目标产品权重

完整交易日 D 的具体合约最终 OI/volume
→ D+1 应交易的具体合约
```

当前交易日 D+1 尚未完成的累计 volume/OI 只用于盘口/风险上下文，**不能重新改变 D 已冻结的主力选择**。

历史 L4 同样使用 D 日选择的同一具体合约承担 D→D+1 收益，因此不把 continuous roll jump 当作 Alpha。

## 5. Signal freshness

`signal_max_age_hours` 不再承担“猜周末/节假日”的主要职责。

生产首先要求：

```text
required_signal_day = completed_activity_snapshot.trading_day
latest OHLC day >= required_signal_day
```

然后才使用 `signal_max_age_hours` 作为未来时间戳/长期停更的第二道门。

- provider 刷新失败但缓存已覆盖 required day：可使用缓存；
- required day 缺失且已有 directional risk：返回 `risk_off`，engine 进入 `REDUCE_ONLY`；
- required day 缺失且账户为空：拒绝新增风险，不制造无意义 Kill Switch；
- 新启动尚无 completed activity snapshot：禁止新增风险，已有风险仍可由 reduction/flatten 管理。

## 6. 合约选择

### Calendar

- point-in-time catalog；
- expiry/front-3/adjacent months；
- 双腿同步和活动度。

### Directional

D+1 合约选择只使用 D 的 `DirectionalActivitySnapshot`：

1. product/exchange 允许；
2. 当时已挂牌；
3. D+1 距到期不少于配置阈值；
4. D 日最终 volume/OI 达标；
5. OI → volume → expiry → symbol 稳定排序。

选出的合约在真正下单前还必须存在 D+1 fresh quote 并通过全部微观结构风控。

`DirectionalActivityStore` 是 market evidence sidecar，不拥有 account/order/fill/position。

## 7. Reduction-first

任何新增风险不可用都不能阻塞确定性风险下降：

```text
读取 Broker 当前持仓
→ 计算 target
→ target=0 / 反转 / 超额 / 可执行换月的旧风险形成 reductions
→ 只提交 reducing FAK
→ 等 Broker 回报并重新读取持仓
→ reductions 消失后才允许 openings
```

若某个非零新目标暂时没有 eligible contract/fresh quote：

- 不新增、不换月；
- 若已有该产品仓位，临时冻结为当前净手数；
- 其它产品的减仓继续执行。

## 8. Risk state

统一状态机仍是：

```text
RUNNING
├─ 需要主动退出已有风险 → REDUCE_ONLY → HALTED
└─ 无法安全继续的严重异常 → HALTED
```

Directional 账户日亏损/高水位回撤等需要退出已有风险时，通过 `REDUCE_ONLY` 持续 flatten，而不是直接 HALTED 后把方向仓位遗留在账户。

## 9. Broker/StateStore 真相与重启

Directional trade callback 先走基础 `TradingEngine._apply_expected_trade()` 更新统一 expected positions；quality callback 只观察，不改仓位。

重启时：

```text
RuntimeState.positions
↔ Broker 完整 position snapshot
```

逐合约今昨、多空完全一致才 reconciled。任何不一致直接 fail-closed。不存在 directional 自己的“策略仓位恢复表”。

## 10. Execution quality

同一 `ExecutionQualityRecorder` 记录：

- pair：candidate / decision / round-trip；
- directional：rebalance / fill / cycle。

Directional cycle 汇总包含 realized turnover、commission、median/p95 slippage bps、tracking error、completion latency、partial/rejected count。真实 fill 只来自 Broker `Trade` callback。

## 11. Production-mechanics acceptance

研究证据分两层，不混称：

1. **float-notional specific-contract L4**：证明冻结 Alpha 在 next-open / roll-safe 历史口径下的收益；
2. **production-mechanics proxy**：相同冻结权重叠加 multiplier、整数手数、contract cap、reduction-first、margin/cash、daily-loss/high-watermark gates。

Proxy 使用冻结产品 multiplier，但历史逐日 Broker margin 不可得，因此 Base/Stress 使用显式统一 margin proxy；该结果不是“精确历史柜台资金曲线”。

## 12. Shadow

Shadow 的市场侧来自真实 CTP catalog/tick/trading day/metadata，订单账户侧来自本地 SimBroker。Directional Shadow 仍走正式 activity snapshot、signal day、target lot、RiskManager 和 quality 生命周期，但不会调用真实 CTP `send_order()`。

## 13. 不增加的系统层

当前不需要数据库、消息队列、Web 服务、微服务或第二账户状态机。后续新增价值应来自未来新数据、真实执行质量和成本，而不是继续扩大系统体量。
