# 100% 年化收益目标：最终历史证据

日期：2026-08-22

## 1. 最终结论

`afuture` 的冻结 Execution-Aligned Directional Portfolio 有两个不同层级的历史结论：

- **研究层**：selection-biased specific-contract / next-open float-notional L4 最近两年 Base 年化 **107.4623%**，达到“已观察历史 100%”目标；
- **生产机械层**：相同冻结权重经过当前 integer lots、margin/cash、daily-loss/high-watermark 等生产账户门后，最近两年 Base 年化约 **6.7861%**，并在 **2024-09-19** 触发日亏损门停机。

所以最终正确表述是：

> **100% 已在选择偏差明确的研究口径达成，但没有在当前 production-equivalent 账户语义下达成。**

该结论不是未来收益保证，也不能用来要求生产风控为了回测数字而自动放宽。

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
- Base 最大回撤约 **27.41%**；
- Stress 年化约 **-31.42%**；
- `pristine_final_oos=false`。

因此策略存在显著 selection bias / regime dependence。

## 3. 冻结策略

- Universe：50 个成熟中国商品期货品种；
- 产品顺序：代码字母序；
- template pool：固定 96；
- family：breakout、time-series momentum、momentum、moving average、reversal、acceleration；
- meta lookback：10；
- meta rebalance：5；
- active templates：3；
- meta evidence：已完成 continuous `open→close` intraday proxy；
- 产品 signal：只使用前一完整交易日及此前历史；
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

规则：historical listing 可见性、point-in-time OI/volume、20 天交割黑窗、同一具体合约 t→t+1、continuous roll jump 不计入 Alpha。

## 5. 官方 GitHub float-L4 证据

- workflow run：`32567558268`；
- tested head：`351b89e73b79b4eee960980ff9a806d89b9d01cd`；
- artifact：`execution-aligned-return-target-evidence`；
- artifact id：`9474502870`；
- SHA-256：`2ab4d6a9547a21659eef2db27b4bbd3278b65557c044468d39c41c584bfea62b`；
- `specific_contracts=true`；
- `roll_safe=true`；
- `max_gross_leverage<=2.0`。

原始 specific-contract 数据 artifact id：`9473260618`。

## 6. Research/live 因果对齐

历史 L4 的选约语义等价于 D 日最终 activity 决定 D+1 具体合约。生产现在同样：

```text
CTP trading day D 最终 OI/volume
→ DirectionalActivitySnapshot
→ D+1 concrete contract
```

D+1 尚未完成的累计 activity 不再重新选主力。

Signal 绑定 `required_signal_day = completed activity day`：

- 普通交易日漏 bar 不会被 96h 容忍掩盖；
- provider 临时失败只有在缓存已覆盖 required day 时才能继续；
- activity snapshot 比已经确认完成的 signal day 更旧时 fail-closed；
- stale/missing required signal + existing risk → `REDUCE_ONLY`。

## 7. Production-mechanics：固定 artifact 独立复核

Production proxy 没有重新搜索模板/参数，也没有重新抓数据。使用：

- artifact `9473260618` 的固定 concrete-contract 原始数据；
- artifact `9474502870` 的最终 `execution_aligned_weights.csv`；
- 当前 `DirectionalProductionAcceptance` 账户机械语义。

统一参数：

```text
initial capital          = 500000
Base cost                = 5bp one-way
Stress cost              = 15bp one-way
Base margin proxy        = 12%
Stress margin proxy      = 15%
margin buffer            = 1.25
max margin ratio         = 35%
min available ratio      = 25%
daily loss limit         = 5%
total drawdown limit     = 30%
max contract volume      = 100
parameter_search         = false
margin_is_historical_truth = false
```

每个报告窗口独立以 500,000 / flat 开始；signal weights 始终来自同一冻结历史，不按窗口重新拟合。

### 最近两年结果

| 指标 | Base | Stress |
|---|---:|---:|
| 年化收益 | **6.7861%** | **3.4290%** |
| 累计收益 | **13.4401%** | **6.6897%** |
| 最大回撤 | **5.5680%** | **5.3020%** |
| Sharpe | 0.8334 | 0.5507 |
| 活跃交易日 | **20 / 484** | **17 / 484** |
| 最终权益 | **567,200.36** | **533,448.53** |
| margin reject days | 0 | **14** |
| first divergence | `daily loss limit reached` | `combined margin ratio would exceed limit` |
| fatal gate | `daily loss limit reached` | `margin ratio limit reached` |
| halt date | **2024-09-19** | **2024-09-19** |

Base 在 2024-09-19 的当日账户路径触发 5% daily-loss gate 后 flatten / halt。Stress 存在更早的 margin opening reject，并最终在 2024-09-19 因 margin ratio gate 退出。

### Production gap

| 指标 | Base | Stress |
|---|---:|---:|
| Float 年化 | 107.4623% | 58.1372% |
| Proxy 年化 | 6.7861% | 3.4290% |
| 年化差值 | **-100.6762 pct-pts** | **-54.7082 pct-pts** |
| Float 累计 | 306.1855% | 141.1415% |
| Proxy 累计 | 13.4401% | 6.6897% |

Proxy 的 5% 左右最大回撤不能被解释成“生产等价后风险显著改善”，主要因为账户非常早就被风险门停止，后续行情不再承担风险。

完整说明见 [`directional-production-mechanics-evidence.md`](directional-production-mechanics-evidence.md)。

## 8. 为什么没有放宽风险门

这轮目标是证明生产真实性，而不是继续拟合历史。当前结果说明真正的第一 divergence 是账户风险权限，不应通过以下方式掩盖：

- 提高 leverage >2x；
- 静默把 daily-loss 5% 调大；
- 静默把 total DD 30% 调大；
- 为避免 margin reject 放宽 35% margin / 25% cash reserve；
- 再扩大 template pool 追同一历史。

任何未来风险阈值变化都应基于实际账户承受能力、Shadow/test/small-capital 新证据独立决策。

## 9. 生产执行边界

Directional 生产还具备：

- account-exclusive；
- previous-day activity sidecar；
- required signal trading-day gate + valid-cache fallback；
- stale activity fail-closed；
- missing new target 不阻塞其它 reduction；
- risk_off + existing risk → `REDUCE_ONLY`；
- reductions 结算后下一 cycle 才允许 openings；
- Broker 是 order/fill/position 唯一真相；
- restart position mismatch fail-closed；
- directional rebalance/fill/cycle execution-quality evidence。

## 10. 仍不能由历史证明的内容

没有多年历史完整：

- bid/ask/depth；
- queue position；
- partial fill / reject；
- CTP/交易所流控；
- 逐日真实 Broker margin；
- 真实结算手续费；
- reduction 成交确认后下一 cycle opening 的真实时间价格。

因此真实资金仍必须经过多日 Shadow、测试柜台和极小真实仓位。107.4623% 不能直接当成可兑现实盘收益，而 6.7861% proxy 也不能当成未来真实收益预测。
