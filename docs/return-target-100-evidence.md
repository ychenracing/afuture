# 100% 年化收益目标：最终历史证据

日期：2026-08-22

## 1. 结论

`afuture` 的冻结 Execution-Aligned Directional Portfolio 在**选择偏差明确存在**的 specific-contract / next-open 历史口径下、gross notional 不超过 2.0x 时，最近两年 Base 年化达到 100% 目标。

该结论只表示“已观察历史目标达成”，不是独立 OOS 证明或未来收益保证。

## 2. 官方 float-notional L4

固定区间：`2024-08-21 ~ 2026-08-20`。

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |
| gross target 上限 | **2.0x** | **2.0x** |

Extreme 30bp：最近两年年化约 **5.09%**，最大回撤约 **43.51%**。

### Final OOS

`2026-02-21 ~ 2026-08-20` 已被此前选择过程观察：

- Base 年化约 **-10.73%**；
- Base 最大回撤约 **24.98%**；
- Stress 年化约 **-31.42%**；
- `pristine_final_oos=false`。

所以该策略有显著 selection bias / regime dependence。

## 3. 冻结策略

- Universe：50 个成熟中国商品期货品种；
- 产品顺序：代码字母序；
- template pool：固定 96；
- family：breakout、time-series momentum、momentum、moving average、reversal、acceleration；
- meta lookback：10；
- meta rebalance：5；
- active templates：3；
- meta evidence：已完成 continuous `open→close` intraday proxy；
- 产品 signal：只使用前一日收盘及此前历史；
- gross target：≤2.0x。

`ExecutionAlignedAggressivePolicy` 是唯一生产 directional signal policy。旧 32-template 中间 policy 已从生产维护面删除。

## 4. 数据真实性

原始 concrete-contract 数据：

```text
products                  = 50
candidate contract calls  = 3000
usable concrete contracts = 2540
specific daily rows       ≈ 495086
missing next returns      = 0 on final products
```

规则：

- historical listing 可见性；
- point-in-time OI/volume；
- 20 天交割黑窗；
- t→t+1 只使用 t 日已选的同一具体合约；
- continuous roll jump 不计入 Alpha。

## 5. 官方 GitHub 证据

原 execution-aligned L4：

- workflow run：`32567558268`；
- tested head：`351b89e73b79b4eee960980ff9a806d89b9d01cd`；
- artifact：`execution-aligned-return-target-evidence`；
- artifact id：`9474502870`；
- SHA-256：`2ab4d6a9547a21659eef2db27b4bbd3278b65557c044468d39c41c584bfea62b`；
- `specific_contracts=true`；
- `roll_safe=true`；
- `max_gross_leverage<=2.0`。

上表中的累计收益、Stress Sharpe 和 Extreme 数字均以该最终 artifact 为准，替换此前 README 中残留的较早候选数字。

## 6. Previous-day production alignment

历史 L4 的选约语义等价于：D 日最终 activity 决定 D+1 具体合约。生产现在使用同一边界：

```text
CTP trading day D 最终 OI/volume
→ DirectionalActivitySnapshot
→ D+1 concrete contract
```

D+1 尚未完成的累计 activity 不再重新选主力。

Signal 也绑定 `required_signal_day = completed activity day`。普通交易日漏 bar 不会再因为 `signal_max_age_hours=96` 而被错误接受。

## 7. Production-mechanics proxy

Float-notional L4 不是某个真实账户逐日资金曲线，因此新增独立 `DirectionalProductionAcceptance`，使用**同一冻结权重**加入：

- frozen product multipliers；
- integer lot floor；
- max contract volume；
- previous-day contract selection；
- roll / reversal reduction-first；
- margin / available cash；
- daily loss；
- high-watermark drawdown；
- risk breach 后不自动重新开仓。

统一假设：

```text
initial capital      = 500000
Base cost            = 5bp one-way
Stress cost          = 15bp one-way
Base margin proxy    = 12%
Stress margin proxy  = 15%
margin buffer        = 1.25
```

历史逐日真实期货公司 margin schedule 不可获得，所以：

```text
margin_is_historical_truth = false
parameter_search            = false
selection_frozen            = true
```

最终 L4 会同时产出 `directional_production_mechanics_report.json`、Base/Stress daily equity 和 `production_gap`。该 proxy 的任务是量化整数手数、multiplier、margin/cash、drawdown gates 对旧 107.4623% 曲线的影响；它不能替代历史 L1 或真实 CTP Shadow。

## 8. 生产执行边界

Directional 生产现在还具备：

- account-exclusive；
- previous-day activity sidecar；
- required signal trading-day gate + valid-cache fallback；
- missing new target 不阻塞其它 reduction；
- stale/missing required signal + existing risk → `REDUCE_ONLY`；
- reductions 结算后下一周期才允许 openings；
- Broker 是 order/fill/position 唯一真相；
- restart position mismatch fail-closed；
- directional rebalance/fill/cycle execution-quality evidence。

## 9. 仍不能由历史证明的内容

没有多年历史完整：

- bid/ask/depth；
- queue position；
- partial fill / reject；
- CTP/交易所流控；
- 逐日真实 Broker margin；
- 真实结算手续费。

因此真实资金仍必须经过多日 Shadow、测试柜台和极小真实仓位。107.4623% 不能直接当成可兑现实盘收益。
