# Directional Production-Mechanics 最终证据

日期：2026-08-22

## 1. 结论

冻结的 Execution-Aligned Directional Portfolio 存在两套必须同时保留、不能混用的历史结论：

1. **Float-notional specific-contract L4**：在明确存在历史选择偏差的口径下，`2024-08-21 ~ 2026-08-20` Base 5bp 年化 **107.4623%**；
2. **Production-mechanics proxy**：把同一冻结权重放入当前账户机械和风险门后，同区间 Base 5bp 年化约 **6.7861%**，并于 **2024-09-19** 触发 `daily loss limit reached` 后 flatten / halt。

因此，**100% 年化历史目标没有在当前 production-account 语义下得到等价证明**。107.4623% 仍是正确的研究结果，但不能再被描述成“当前生产代码按当前风控运行可复现的历史收益”。

本轮没有为了恢复 100% 数字而放宽 `max_daily_loss_ratio=5%`、`max_total_drawdown_ratio=30%`、`max_margin_ratio=35%`、`min_available_ratio=25%` 或 2.0x gross 上限。

## 2. 证据来源与可重复性

本次 production-mechanics 复核没有重新搜索模板、参数或品种，也没有重新抓取历史数据。

使用固定证据：

- 原 specific-contract 原始数据 artifact：`return-target-specific-evidence`，artifact id `9473260618`；
- 最终冻结 96-template 权重 artifact：`execution-aligned-return-target-evidence`，artifact id `9474502870`；
- 原始 concrete-contract 数据约 495,086 行、50 品种；
- 冻结 `execution_aligned_weights.csv` 最大 target gross = 2.0x；
- 当前 `DirectionalProductionAcceptance` 生产机械语义；
- 参数搜索：`false`；
- margin 历史真值：`false`。

Float L4 与固定 artifact 精确复核得到：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | 107.4623% | 58.1372% |
| 累计收益 | 306.1855% | 141.1415% |
| 最大回撤 | 27.4097% | 32.9554% |
| Sharpe | 1.6874 | 1.1525 |

这证明 production proxy 使用的是同一冻结策略证据，而不是另一套重新拟合的权重。

## 3. Production-mechanics 假设

每个报告窗口是独立账户实验：

- 初始资金：500,000；
- 窗口开始：flat；
- signal weights：来自同一完整冻结历史，不按窗口重新拟合；
- previous-day activity 选择下一交易日 concrete contract；
- frozen product multiplier；
- integer lot floor；
- `max_contract_volume=100`；
- reduction-first；
- Base cost 5bp one-way；
- Stress cost 15bp one-way；
- Base margin proxy 12% × `margin_estimate_buffer=1.25`；
- Stress margin proxy 15% × `margin_estimate_buffer=1.25`；
- `max_margin_ratio=35%`；
- `min_available_ratio=25%`；
- `max_daily_loss_ratio=5%`；
- `max_total_drawdown_ratio=30%`；
- 风险门触发后 flatten 并停止该独立账户路径自动重新开仓。

注意：历史逐日真实期货公司保证金率不可得，因此 margin 是显式 proxy，不是伪造的历史柜台真值。

## 4. 最近两年 Production-mechanics 结果

区间：`2024-08-21 ~ 2026-08-20`，484 个报告交易日。

| 指标 | Base 5bp / 12% margin proxy | Stress 15bp / 15% margin proxy |
|---|---:|---:|
| 年化收益 | **6.7861%** | **3.4290%** |
| 累计收益 | **13.4401%** | **6.6897%** |
| 最大回撤 | **5.5680%** | **5.3020%** |
| Sharpe | 0.8334 | 0.5507 |
| 活跃交易日 | **20 / 484** | **17 / 484** |
| 最终权益 | **567,200.36** | **533,448.53** |
| margin reject days | 0 | **14** |
| 首个 divergence | `daily loss limit reached` | `combined margin ratio would exceed limit` |
| 最终致命风险门 | `daily loss limit reached` | `margin ratio limit reached` |
| 致命风险门日期 | **2024-09-19** | **2024-09-19** |

Base 在 2024-09-19 约 -5.30% 的当日账户路径后触发 5% 日亏损门，随后 flatten / halt。Stress 更早出现 margin opening reject，并最终在同日触发 margin ratio 风险门后退出。

### 不能误读最大回撤

Production proxy 的 5% 左右最大回撤**不是**证明策略在真实账户下天然比 27% float L4 更稳。主要原因是账户在 2024-09-19 已被风险门停止，后续大部分历史行情不再承担风险。

因此应同时看：

- 年化收益；
- active days；
- first divergence；
- halt date；
- margin reject days；
- 最大回撤。

单看较小回撤会得出错误结论。

## 5. Float L4 → Production proxy 差距

| 指标 | Base | Stress |
|---|---:|---:|
| Float 年化 | 107.4623% | 58.1372% |
| Production proxy 年化 | 6.7861% | 3.4290% |
| 年化差值 | **-100.6762 pct-pts** | **-54.7082 pct-pts** |
| Float 累计 | 306.1855% | 141.1415% |
| Production proxy 累计 | 13.4401% | 6.6897% |
| Float 最大回撤 | 27.4097% | 32.9554% |
| Production proxy 最大回撤 | 5.5680% | 5.3020% |

核心差距不是某一个手续费数字，而是：**当前生产账户风险门会在研究路径早期主动停止风险暴露**。这说明“研究 target weights 的完整历史收益”与“当前生产账户状态机允许实际承担的风险路径”并不等价。

## 6. 其它窗口摘要

Base proxy 年化：

- prior1：约 -3.19%；
- prior2：约 -20.30%；
- train：约 14.03%；
- validation：约 34.94%；
- selection_full：约 9.20%；
- OOS：约 2.64%。

Stress proxy 年化：

- prior1：约 -7.34%；
- prior2：约 -2.79%；
- train：约 6.98%；
- validation：约 -6.34%；
- selection_full：约 4.62%；
- OOS：约 -1.21%。

各窗口独立从 500,000 / flat 开始，因此这些数字用于比较窗口内账户机械影响，不是把多个窗口串成一条连续真实账户曲线。

## 7. 仍然不是精确实盘重放

Production proxy 比 float-notional L4 更接近账户语义，但仍缺：

- 多年历史 L1 bid/ask/depth；
- queue position；
- partial fill / reject；
- 真实 CTP/交易所流控；
- 历史逐日 Broker margin schedule；
- 实际结算手续费；
- reduction 成交确认后的真实下一时刻 opening 价格。

日线 proxy 在同一交易日只能用日线 open/close 表达执行阶段，不能精确模拟“reduction FAK 成交后下一 runtime cycle 再 opening”的分钟/秒级时点。因此该结果仍不能替代 CTP Shadow、测试柜台和极小真实资金。

## 8. 对生产决策的含义

当前正确状态是：

- 冻结 Alpha 的 selection-biased float L4 **达到** 100% 历史目标；
- 当前 production-mechanics proxy **没有达到** 100%；
- 主要阻断来自现有账户风险门，而不是因为需要再增加 Alpha/template/leverage；
- 不应为了回测数字静默放宽风险门；
- 下一阶段最有信息价值的是 Shadow/test/small-capital 的 realized turnover、slippage、commission、margin 和 risk-off 行为。

如果未来要重新讨论生产风险阈值或目标 gross，应以真实账户风险承受能力和新的未见执行证据为依据，而不是以“必须把历史回测恢复到 100%”为依据。
