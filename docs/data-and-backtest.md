# 数据、回放与研究

## 1. 数据职责

### Calendar / Auto

生产与回放以带时区 Tick 为基础；Auto 还使用 limit、volume、open_interest、point-in-time catalog 和合约 expiry/listing。

### Directional

Directional 明确分离三类证据：

1. **continuous OHLC**：只产生产品 signal 和已完成 intraday meta score；
2. **concrete-contract daily OHLC/OI/volume**：验证真实换月和 next-open 收益；
3. **CTP L1 / account / fill**：生产 Shadow/实盘执行证据。

公开历史没有多年完整 bid/ask/depth/queue/partial/reject，因此日线 L4 和 production-mechanics proxy 都不能替代未来真实执行。

## 2. 因果规则

正式 directional 研究/生产遵守：

- t 日未完成信息不能决定已经发生的 t 日收益；
- continuous roll jump 不计入可交易 Alpha；
- historical universe 只能包含当时已挂牌合约；
- t→t+1 收益来自 t 日已选择的**同一具体合约**；
- D+1 的具体合约选择使用 D 的最终 OI/volume，不使用 D+1 尚未完成 activity；
- Final OOS 被任何选择过程观察过后必须标记 non-pristine。

最终 float-notional 执行分解：

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
- 当前 tick 只负责 fresh quote、spread、depth、limit 等执行门。

第一次启动还没有 completed snapshot 时，不新增 directional 风险。

## 4. Signal trading-day gate

`required_signal_day = completed_activity_snapshot.trading_day`。OHLC 最新日期必须覆盖 required day；`signal_max_age_hours` 只是第二层长时间停更门。

这解决了“96 小时为了兼容周末，却在普通工作日容忍漏掉完整交易日”的问题。

## 5. 历史研究结论

### corrected M/OI calendar

旧同品种 M/OI 研究仍未通过高收益门：

```text
prior-forward             4 trades, -1.958R
final OOS                  2 trades, +0.296R
recent two years           5 trades, +1.028R
neighbor stability         0 / 16
2% risk proxy annualized   ≈ 1.07%
```

### Broad directional L3

约 50 个成熟商品期货连续合约用于 family/候选发现。连续合约 L3 只能用于研究，因为 close-to-close 和 roll 语义会高估可执行收益。

## 6. Final specific-contract L4

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
- gross ≤2x。

官方 artifact `2024-08-21 ~ 2026-08-20`：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |

Extreme 30bp：最近两年年化约 **5.09%**、最大回撤约 **43.51%**。

Final OOS `2026-02-21 ~ 2026-08-20` 已被观察：Base 年化约 `-10.73%`、Base 最大回撤约 `24.98%`、Stress 年化约 `-31.42%`。因此：

```text
selection_bias_acknowledged = true
pristine_final_oos          = false
```

107.4623% 只能称为允许有限历史选择偏差后的已观察目标结果。

## 7. Production-mechanics proxy

旧 L4 的主要剩余差异是 float notional 与真实账户机械约束。新增 proxy 使用**完全相同的冻结产品权重**，不搜索参数，并逐日加入：

1. previous-day activity 选 next-day concrete contract；
2. prior lots 的 previous-close → current-open PnL；
3. open 时 reduction-first；
4. 真实冻结 product multiplier；
5. `floor(equity × abs(weight) / (open × multiplier))`；
6. `max_contract_volume`；
7. margin / available cash；
8. open→close PnL；
9. day-start loss 与 high-watermark drawdown；
10. risk breach 后永久停止自动重新开仓。

默认账户初始资金 `500000`。历史逐日期货公司真实 margin schedule 不存在，所以明确使用：

```text
Base margin proxy   = 12%
Stress margin proxy = 15%
margin buffer       = 1.25
```

报告必须同时输出旧 float L4 与 production proxy，并给出 `production_gap`、first divergence、margin reject days 和 realized gross。这个 proxy 是账户机械层压力测试，不是精确 CTP 历史重放。

## 8. 成本与成交限制

即使 production proxy 通过，仍缺少：

- 历史完整 bid/ask/depth；
- queue position；
- partial fill / reject；
- 真实订单流控；
- 逐笔结算手续费；
- 各品种实际第一个可成交时刻。

因此真正的“历史 Alpha 被执行吞掉多少”最终要由 directional quality + Shadow + 测试柜台 + 小资金回答。

## 9. 验证层级

```text
L1  activity/signal/lot/risk/quality 因果单测
L2  manager/engine/restart smoke
L3  production-mechanics proxy / broad research
L4  final specific-contract roll-safe evidence
Final Python 3.10/3.13 CI + review
```

昂贵 L4 只在策略公式、合约选择、执行时点、成本或数据方法实质变化后运行。诊断性小修先跑受影响测试。

## 10. 后续最有价值的新信息

1. 新发生、此前未参与选择的交易日；
2. 多交易日 CTP Shadow；
3. directional `planned vs realized turnover/slippage/commission/tracking`；
4. 测试柜台 FAK/partial/reject/reconnect；
5. 极小真实仓位；
6. 若可获得，可靠历史 L1。
