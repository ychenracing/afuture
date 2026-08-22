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
→ 冻结产品权重，gross target <=2x

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

`execution_aligned_policy.py` 自包含最终 signal/meta helper。`directional.py` 只保留配置、合约选择、整数手数和 rebalance 原语。

## 4. 因果时间边界

Directional 同时冻结：

```text
完整交易日 D 的 OHLC
→ D+1 目标产品权重

完整交易日 D 的具体合约最终 OI/volume
→ D+1 具体合约
```

D+1 尚未完成的累计 volume/OI 不能重新改变 D 已冻结的主力选择。若持久化 activity snapshot 比已经确认完成的 signal trading day 更旧，也不能继续开仓。

历史 L4 同样使用 D 日选择的同一具体合约承担 D→D+1 收益，因此不把 continuous roll jump 当作 Alpha。

## 5. Signal freshness

生产优先要求：

```text
required_signal_day = completed_activity_snapshot.trading_day
latest OHLC day >= required_signal_day
```

然后才使用 `signal_max_age_hours` 作为未来 timestamp/长期停更的第二道门。

- provider 失败但 cache 已覆盖 required day：可继续；
- required day 缺失且已有 risk：`risk_off → REDUCE_ONLY`；
- required day 缺失且账户为空：拒绝新增；
- activity snapshot 明显 stale：fail-closed；
- 新启动无 completed snapshot：禁止新增风险。

## 6. Reduction-first

```text
Broker 当前持仓
→ target=0 / 反转 / 超额 / 可执行换月 reductions
→ reducing FAK
→ Broker 回报
→ 下一 cycle 重新读取真实持仓
→ 无 reductions 才允许 openings
```

新目标暂时没有 eligible contract/fresh quote 时：不新增、不换月；已有同产品仓位冻结当前净手数；其它确定性 reductions 继续。

## 7. Risk state

统一状态机：

```text
RUNNING
├─ 需要主动退出已有风险 → REDUCE_ONLY → HALTED
└─ 无法安全继续的严重异常 → HALTED
```

Directional 账户日亏损、drawdown、margin 等需要退出已有风险时，先通过 `REDUCE_ONLY` flatten，而不是直接 HALTED 后遗留方向仓位。

## 8. Broker/StateStore 真相与重启

Directional trade callback 先走基础 `TradingEngine._apply_expected_trade()` 更新统一 expected positions；quality callback 只观察，不改仓位。

重启：

```text
RuntimeState.positions
↔ Broker complete position snapshot
```

逐合约今昨、多空完全一致才 reconciled；任何 mismatch fail-closed。

## 9. Execution quality

同一 `ExecutionQualityRecorder` 记录：

- pair：candidate / decision / round-trip；
- directional：rebalance / fill / cycle。

Directional cycle 汇总 realized turnover、commission、median/p95 slippage bps、tracking error、completion latency、partial/rejected count。真实 fill 只来自 Broker `Trade` callback。

## 10. 两层经济证据

架构上明确把收益证据分层，防止一个数字跨层级误用：

1. **Float-notional specific-contract L4**：冻结 Alpha 在 next-open / roll-safe 历史口径 Base 年化 107.4623%；
2. **Production-mechanics proxy**：同一冻结权重叠加 integer lots、multiplier、contract cap、reduction-first、margin/cash、daily-loss/high-watermark gates，Base 年化约 6.7861%，2024-09-19 daily-loss halt。

第二层证明当前账户状态机允许承担的历史风险路径与第一层不同。Proxy 小回撤主要来自早停机，不能解释为策略天然更稳。

历史逐日 Broker margin 不可得，因此 Base/Stress 使用显式统一 12%/15% margin proxy × 1.25 buffer，不声称是精确柜台历史。

详细证据：[`directional-production-mechanics-evidence.md`](directional-production-mechanics-evidence.md)。

## 11. Shadow

Shadow 市场侧来自真实 CTP catalog/tick/trading day/metadata，账户侧来自本地 SimBroker。Directional Shadow 走正式 activity snapshot、signal day、target lot、RiskManager 和 quality 生命周期，但不调用真实 CTP `send_order()`。

Production proxy 已经显示风险门是主要 divergence，因此 Shadow 必须重点观察 actual gross、margin、daily loss、risk-off、realized cost 与 target tracking，而不仅是程序稳定性。

## 12. 不增加的系统层

当前不需要数据库、消息队列、Web 服务、微服务或第二账户状态机。后续新增价值应来自未来新数据、真实执行质量和风险/收益证据，而不是继续扩大系统体量或对同一历史增加模板。
