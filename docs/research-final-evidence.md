# afuture 研究证据总览

本文件保留 2026-08-21~2026-08-22 的主要研究结论。旧套利专项没有被删除或改写；Execution-Aligned Directional 是另一条账户互斥策略链。

## 1. 全局结论

研究最终形成两个事实：

1. corrected M/OI、经济 pair、BU/FU、intraday、结构套利等市场中性路线没有接近 100% 年化；
2. 在用户允许有限历史选择偏差、gross≤2x 的前提下，50 品种 directional 组合在 final specific-contract / next-open 历史口径达到 **107.4623% 年化 / 27.4097% 最大回撤**。

第二个结果不是独立泛化证明：Final OOS 已被观察且为负。

最新正式收益证据以 [`return-target-100-evidence.md`](return-target-100-evidence.md) 为准。

## 2. corrected M/OI calendar

修正中国期货 trading day、同步采样、front-3、20 天黑窗、historical listing 和 point-in-time volume 后：

```text
prior-forward             4 trades, -1.958R
final OOS                  2 trades, +0.296R
recent two years           5 trades, +1.028R
neighbor stability         0 / 16
2% risk proxy annualized   ≈ 1.07%
```

所以旧同品种策略没有通过高收益经济门。

## 3. 其它套利研究

研究过：

- cross-sectional / market-neutral momentum、reversal、skewness；
- rolling residual / beta / OU half-life / regime；
- P/Y、PP/V、AL/ZN、BU/FU、CU/AL、J/JM 等经济关系；
- 60 分钟 intraday；
- soybean crush、steel/coke、polymer/base-metals 等结构关系。

其中 BU/FU specific-contract 信号证明并非单纯 continuous roll 假象，但收益仍远低于目标；失败 intraday/structural 实验不进入生产维护面。

## 4. Directional 收益优先阶段

研究 family：

- breakout；
- time-series momentum；
- momentum；
- moving average；
- reversal；
- acceleration / slow-fast。

连续合约 L3 首先发现高收益候选，但初版 specific-contract next-open 明显衰减，暴露理论目标与实际执行错位。最终策略把模板筛选/meta evidence 对齐到 execution-aware 历史，并明确承认 selection bias。

## 5. Final execution-aligned L4

固定原始数据：

```text
products                  = 50
candidate contract calls  = 3000
usable concrete contracts = 2540
specific daily rows       ≈ 495086
missing next returns      = 0 on final products
```

冻结策略：

- 96-template pool；
- meta lookback=10；
- meta rebalance=5；
- meta count=3；
- completed continuous intraday proxy 只用于 causal meta ranking；
- point-in-time concrete-contract selection；
- 20 天黑窗；
- gross≤2x。

`2024-08-21 ~ 2026-08-20` 官方 final artifact：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |

Extreme 30bp：年化约 **5.09%**，最大回撤约 **43.51%**。

Final OOS `2026-02-21 ~ 2026-08-20`：Base 年化约 `-10.73%`、Base 最大回撤约 `24.98%`、Stress 年化约 `-31.42%`，因此 `pristine_final_oos=false`。

## 6. 生产真实性收口

后续代码没有继续扩大 Alpha/template/leverage，而是收口 research/live 差异：

- D 日 completed activity 决定 D+1 concrete contract；
- required signal trading day 取 completed activity day；
- provider cache 只有覆盖 required day 才能容错；
- stale/missing required signal + existing risk → `REDUCE_ONLY`；
- missing new target 不阻塞其它 reductions；
- Broker 仍是唯一 order/fill/position truth；
- restart mismatch fail-closed；
- directional rebalance/fill/cycle execution quality；
- production-mechanics proxy 加入 multiplier、integer lots、contract cap、margin/cash、daily-loss/high-watermark。

最终生产 signal policy 只有 `ExecutionAlignedAggressivePolicy`；旧 32-template 中间 policy 已移除。

## 7. Production-mechanics proxy 边界

该 proxy 使用相同冻结权重，不做参数搜索。历史逐日真实 Broker margin schedule 不可得，因此 Base/Stress 使用显式 12%/15% margin proxy × 1.25 buffer。

它用于量化 float-notional L4 到账户机械层的差距，不能替代真实 L1、partial/reject、CTP 流控、结算手续费和未来 Shadow。

## 8. 当前最重要的新证据

不再继续扩大已观察历史参数空间。后续高信息价值证据依次是：

1. 新发生未来数据；
2. 多日 CTP Shadow；
3. realized turnover/slippage/commission/tracking；
4. 测试柜台真实订单生命周期；
5. 极小真实仓位。
