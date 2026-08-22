# afuture 研究证据总览

本文件保留 2026-08-21~2026-08-22 的主要研究结论。旧套利专项没有被删除或改写；Execution-Aligned Directional 是另一条账户互斥策略链。

## 1. 全局结论

研究最终形成三个必须同时成立的事实：

1. corrected M/OI、经济 pair、BU/FU、intraday、结构套利等市场中性路线没有接近 100% 年化；
2. 50 品种 directional 在明确允许历史选择偏差、gross≤2x 的 specific-contract / next-open float-notional 口径达到 **107.4623% 年化 / 27.4097% 最大回撤**；
3. 相同冻结权重经过当前生产账户机械和风险门后，最近两年 production proxy Base 只有 **6.7861% 年化**，并于 **2024-09-19** 触发 5% daily-loss gate 后停机。

因此“研究历史达到 100%”是事实，但“当前生产账户语义已经达到 100%”不是事实。Final OOS 已被观察且为负，也没有独立泛化证明。

最新正式证据：

- [`return-target-100-evidence.md`](return-target-100-evidence.md)
- [`directional-production-mechanics-evidence.md`](directional-production-mechanics-evidence.md)

## 2. corrected M/OI calendar

修正中国期货 trading day、同步采样、front-3、20 天黑窗、historical listing 和 point-in-time volume 后：

```text
prior-forward             4 trades, -1.958R
final OOS                  2 trades, +0.296R
recent two years           5 trades, +1.028R
neighbor stability         0 / 16
2% risk proxy annualized   ≈ 1.07%
```

旧同品种策略没有通过高收益经济门。

## 3. 其它套利研究

研究过 cross-sectional / market-neutral momentum、reversal、skewness；rolling residual / beta / OU half-life / regime；P/Y、PP/V、AL/ZN、BU/FU、CU/AL、J/JM 等经济关系；60 分钟 intraday；soybean crush、steel/coke、polymer/base-metals 等结构关系。

BU/FU specific-contract 信号证明并非单纯 continuous roll 假象，但收益仍远低于目标；失败 intraday/structural 实验不进入生产维护面。

## 4. Directional 收益优先阶段

研究 family：breakout、time-series momentum、momentum、moving average、reversal、acceleration / slow-fast。

连续合约 L3 首先发现高收益候选，但初版 specific-contract next-open 明显衰减，暴露理论目标与实际执行错位。最终策略把模板筛选/meta evidence 对齐到 execution-aware 历史，并明确承认 selection bias。

## 5. Final float-notional L4

固定原始数据：

```text
products                  = 50
candidate contract calls  = 3000
usable concrete contracts = 2540
specific daily rows       ≈ 495086
missing next returns      = 0 on final products
```

冻结策略：96-template pool、meta lookback=10、meta rebalance=5、meta count=3、completed continuous intraday proxy causal ranking、point-in-time concrete-contract selection、20 天黑窗、gross≤2x。

`2024-08-21 ~ 2026-08-20` 官方 final artifact：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |

Extreme 30bp：年化约 **5.09%**、最大回撤约 **43.51%**。

Final OOS `2026-02-21 ~ 2026-08-20`：Base 年化约 `-10.73%`、Base 最大回撤约 `27.41%`、Stress 年化约 `-31.42%`，因此 `pristine_final_oos=false`。

## 6. 生产真实性收口

后续没有继续扩大 Alpha/template/leverage，而是收口 research/live 差异：

- D 日 completed activity 决定 D+1 concrete contract；
- required signal trading day = completed activity day；
- provider cache 只有覆盖 required day 才能容错；
- stale persisted activity 不能掩盖更新的完整 signal day；
- stale/missing required signal + existing risk → `REDUCE_ONLY`；
- missing new target 不阻塞其它 reductions；
- Broker 仍是唯一 order/fill/position truth；
- restart mismatch fail-closed；
- directional rebalance/fill/cycle execution quality；
- production-mechanics 加入 multiplier、integer lots、contract cap、margin/cash、daily-loss/high-watermark。

最终生产 signal policy 只有 `ExecutionAlignedAggressivePolicy`；旧 32-template 中间 policy 已移除。

## 7. Production-mechanics 结果

固定 artifact + 当前 mechanics、无重新拟合/抓取的最近两年结果：

| 指标 | Base | Stress |
|---|---:|---:|
| 年化 | **6.7861%** | **3.4290%** |
| 累计 | **13.4401%** | **6.6897%** |
| 最大回撤 | **5.5680%** | **5.3020%** |
| active days | **20 / 484** | **17 / 484** |
| margin reject days | 0 | **14** |
| fatal gate | daily loss | margin ratio |
| halt date | **2024-09-19** | **2024-09-19** |

Base 12% margin proxy、Stress 15%，都乘 1.25 buffer；当前生产硬门保持 daily loss 5%、total DD 30%、max margin 35%、min available 25%。

这证明当前主要 production gap 来自账户风险权限会很早终止风险路径，而不是只来自整数手数或手续费。

较小的 proxy 最大回撤不能解释成“策略变稳”，因为 proxy 在 2024-09-19 之后基本不再持有风险。

## 8. 为什么不继续历史调参

当前最差的做法是为了恢复 production proxy 100% 去：

- 扩大模板池；
- 提高 gross >2x；
- 静默放宽 5% daily loss / 30% DD；
- 静默放宽 margin/cash gate。

这些都会把“生产真实性检查”重新变成“对同一历史追数字”。因此本轮明确停止这种方向。

## 9. 当前最重要的新证据

后续高信息价值证据依次是：

1. 新发生未来数据；
2. 多日 CTP Shadow；
3. realized turnover/slippage/commission/tracking；
4. 真实 Broker margin/risk-off 行为；
5. 测试柜台真实订单生命周期；
6. 极小真实仓位。

未来如需调整生产风险参数，应基于这些新证据，而不是以必须恢复 107.4623% 为目标。
