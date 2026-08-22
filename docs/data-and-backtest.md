# 数据、回放与研究

## 1. 数据职责

### Calendar / Auto

生产与回放以带时区 Tick 为基础；Auto 还使用 limit、volume、open_interest、point-in-time catalog 和合约 expiry/listing。

### Directional

Directional 分离三类证据：

1. **continuous OHLC**：只产生产品 signal 和已完成 intraday meta score；
2. **concrete-contract daily OHLC/OI/volume**：验证真实换月和 next-open 收益，并驱动 production-mechanics proxy；
3. **CTP L1 / account / fill**：未来 Shadow/实盘执行证据。

公开历史没有多年完整 bid/ask/depth/queue/partial/reject，因此日线 L4 和 production-mechanics proxy 都不能替代真实执行。

## 2. 因果规则

正式 directional 研究/生产遵守：

- t 日未完成信息不能决定已经发生的 t 日收益；
- continuous roll jump 不计入可交易 Alpha；
- historical universe 只能包含当时已挂牌合约；
- t→t+1 收益来自 t 日已选择的**同一具体合约**；
- D+1 的具体合约选择使用 D 的最终 OI/volume，不使用 D+1 尚未完成 activity；
- completed activity snapshot 不能落后于已经确认完成的 signal trading day；
- Final OOS 被任何选择过程观察过后必须标记 non-pristine。

Float-notional L4 执行分解：

```text
截至 t 收盘历史 → t+1 target weights
old weights × (t close → t+1 open)
+ new weights × (t+1 open → close)
- t+1 open turnover cost
```

## 3. Previous-day activity evidence

生产 `DirectionalActivityTracker` 按 `Tick.trading_day` 聚合合约最后可见 volume/OI；只有观察到 trading day 从 D 推进到下一日时，才把 D 冻结为 `DirectionalActivitySnapshot` 并原子写入 JSON sidecar。

下一交易日：

- selector 读取 completed snapshot；
- listing/expiry 按计划交易日过滤；
- OI → volume → expiry → symbol 排序；
- 当前 tick 只负责 fresh quote、spread、depth、limit、价格和实际下单。

第一次启动没有 completed snapshot 时不新增 directional 风险。若持久化 snapshot 已陈旧而 OHLC 已证明存在更新的完整交易日，也 fail-closed，不能用陈旧 activity 开新风险。

## 4. Signal trading-day gate

`required_signal_day = completed_activity_snapshot.trading_day`。OHLC 最新日期必须覆盖 required day；`signal_max_age_hours` 是第二层长时间停更门。

这解决两个问题：

- 96 小时周末容忍不会掩盖普通工作日漏 bar；
- 重启后陈旧 activity snapshot 不能掩盖更新的完整 signal day。

## 5. 历史研究结论

### corrected M/OI calendar

```text
prior-forward             4 trades, -1.958R
final OOS                  2 trades, +0.296R
recent two years           5 trades, +1.028R
neighbor stability         0 / 16
2% risk proxy annualized   ≈ 1.07%
```

旧同品种 M/OI 研究仍未通过高收益门。

### Broad directional L3

约 50 个成熟商品期货连续合约用于 family/候选发现。连续合约 L3 只能用于研究，因为 close-to-close 和 roll 语义会高估可执行收益。

## 6. Final float-notional specific-contract L4

固定原始数据：

```text
products                  = 50
candidate contract calls  = 3000
usable concrete contracts = 2540
specific daily rows       ≈ 495086
missing next returns      = 0 on final products
```

冻结参数：

- 96-template pool；
- breakout / tsmom / momentum / moving-average / reversal / acceleration；
- meta lookback=10；
- meta rebalance=5；
- active templates=3；
- meta score source=`continuous_intraday_proxy`；
- Base 5bp；Stress 15bp；Extreme 30bp；
- gross target ≤2x。

官方 artifact `2024-08-21 ~ 2026-08-20`：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |

Extreme 30bp：最近两年年化约 **5.09%**、最大回撤约 **43.51%**。

Final OOS `2026-02-21 ~ 2026-08-20` 已被观察：Base 年化约 `-10.73%`、Base 最大回撤约 `27.41%`、Stress 年化约 `-31.42%`。因此：

```text
selection_bias_acknowledged = true
pristine_final_oos          = false
```

107.4623% 只能称为允许历史选择偏差后的已观察研究结果。

## 7. Production-mechanics proxy：实际结果

Proxy 使用完全相同的冻结产品权重，不搜索参数，并加入：

1. previous-day activity 选 next-day concrete contract；
2. prior lots 的 previous-close → current-open PnL；
3. open 时 reduction-first；
4. frozen product multiplier；
5. integer lot floor；
6. `max_contract_volume=100`；
7. margin / available cash；
8. open→close PnL；
9. day-start loss 与 high-watermark drawdown；
10. risk breach 后 flatten / halt，不自动重新开仓。

每个统计窗口独立从 `500000` / flat 开始；signal weights 仍来自同一完整冻结历史，不按窗口重新拟合。

历史逐日期货公司真实 margin schedule 不存在，因此：

```text
Base margin proxy   = 12% × 1.25 buffer
Stress margin proxy = 15% × 1.25 buffer
max margin ratio    = 35%
min available       = 25%
daily loss          = 5%
total drawdown      = 30%
```

### 最近两年结果

| 指标 | Base | Stress |
|---|---:|---:|
| 年化 | **6.7861%** | **3.4290%** |
| 累计 | **13.4401%** | **6.6897%** |
| 最大回撤 | **5.5680%** | **5.3020%** |
| 活跃交易日 | **20 / 484** | **17 / 484** |
| 最终权益 | **567,200.36** | **533,448.53** |
| margin reject days | 0 | **14** |
| fatal gate | `daily loss limit reached` | `margin ratio limit reached` |
| halt date | **2024-09-19** | **2024-09-19** |

Base 的生产代理在 2024-09-19 触发 5% 日亏损门后停止；Stress 更早有 opening margin reject，最终同日在 margin ratio gate 下退出。

因此当前生产账户语义**没有复现 100% 年化**。较小的 proxy 最大回撤主要来自很早停机，不能解释为“同一策略在生产环境天然更低回撤”。

详细证据见 [`directional-production-mechanics-evidence.md`](directional-production-mechanics-evidence.md)。

## 8. Production gap

最近两年：

| 指标 | Base | Stress |
|---|---:|---:|
| Float 年化 | 107.4623% | 58.1372% |
| Proxy 年化 | 6.7861% | 3.4290% |
| 年化差值 | -100.6762 pct-pts | -54.7082 pct-pts |
| Float 累计 | 306.1855% | 141.1415% |
| Proxy 累计 | 13.4401% | 6.6897% |

主要差距来自账户状态机允许承担的风险路径，而不仅是手续费或整数手数。

## 9. Proxy 仍非精确 CTP 历史重放

仍缺：

- 历史完整 bid/ask/depth；
- queue position；
- partial fill / reject；
- 真实订单流控；
- 逐日真实 Broker margin；
- 实际结算手续费；
- reduction 成交后下一 runtime cycle opening 的精确分钟/秒价格。

日线 proxy 只能用同一交易日 open/close 近似阶段执行，所以真正的“Alpha 被执行吞掉多少”最终仍需 directional quality + Shadow + 测试柜台 + 小资金回答。

## 10. 验证层级

```text
L1  activity/signal/lot/risk/quality 因果单测
L2  manager/engine/restart smoke
L3  broad research / production-mechanics proxy
L4  final specific-contract roll-safe evidence
Final Python 3.10/3.13 CI + review
```

昂贵 L4 不在诊断性小修后重复。当前 production proxy 已证明最重要的 research/live 差距；下一步不应靠扩大已观察历史参数空间掩盖该差距。

## 11. 后续最有价值的新信息

1. 新发生、此前未参与选择的交易日；
2. 多交易日 CTP Shadow；
3. `planned vs realized turnover/slippage/commission/tracking`；
4. 实际 margin/risk-off 行为；
5. 测试柜台 FAK/partial/reject/reconnect；
6. 极小真实仓位；
7. 若可获得，可靠历史 L1。
