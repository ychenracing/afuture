# 100% 年化收益目标：最终历史证据

日期：2026-08-22

## 结论

本轮把收益目标从低收益同品种跨期均值回归扩展为**账户独占的多品种方向组合**，并在不超过 **2.0x gross notional** 的约束下，以具体合约、真实换月和次交易时段开盘执行口径完成验证。

最终选择偏差明确存在，但用户允许有限历史过拟合；因此本文只陈述“已观察历史目标达成”，不把它描述成独立 OOS 证明或未来收益保证。

### 最终 specific-contract / next-open 结果

固定区间 `2024-08-21 ~ 2026-08-20`，5bp 单边基础成本：

| 指标 | 结果 |
|---|---:|
| 年化收益 | **107.46%** |
| 累计收益 | **317.54%** |
| 最大回撤 | **27.41%** |
| 年化波动 | 63.81% |
| Sharpe | 1.69 |
| 活跃交易日 | 481 / 484 |
| 最大 gross notional | **2.0x** |

15bp 单边压力成本：

| 指标 | 结果 |
|---|---:|
| 年化收益 | **58.14%** |
| 累计收益 | **142.94%** |
| 最大回撤 | **32.96%** |
| Sharpe | 1.22 |

30bp 单边极端成本下，最近两年年化约 9.96%，仍为正，但回撤约 46.76%。

### Final OOS

`2026-02-21 ~ 2026-08-20` 已被此前多轮研究观察，不再是 pristine holdout：

- base 年化：**-10.73%**；
- base 最大回撤：**24.98%**；
- stress 年化：**-31.42%**；
- `pristine_final_oos=false`。

因此本策略具有明显的选择偏差和行情依赖。达到 100% 的含义是“用户允许有限过拟合后，最近两年历史目标已达到”，不是“独立泛化已证明”。

## 冻结策略

最终生产研究策略固定为：

- Universe：50 个成熟中国商品期货品种；
- 产品顺序：**按产品代码字母序冻结**，避免 stable tie-breaking 因配置顺序改变策略；
- 模板池：specific-contract next-open 历史表现选择后的固定 **96** 个模板；
- Alpha family：breakout、time-series momentum、momentum、moving average、reversal、acceleration；
- meta lookback：10 个交易日；
- meta rebalance：5 个交易日；
- 同时激活模板：3；
- meta score：只使用**已经完成的连续合约 open→close 日内代理收益**；不把连续合约隔夜换月 gap 当成 meta 收益；
- 产品信号：只使用前一日收盘及此前历史；
- 具体合约：按 point-in-time OI/volume 选择主力；
- 交割黑窗：20 天；
- 真实执行回测：旧权重承担前收→次开 gap，新目标权重承担次开→收盘收益；
- gross notional：硬上限 2.0x。

## 数据真实性

原始 specific-contract L4 数据由 GitHub Actions run `32562548653` 抓取：

- 50 个品种；
- 3,000 个候选合约请求；
- 2,540 个可用具体合约；
- 约 495,086 行具体合约日线；
- 所有最终产品 `missing_next_contract_returns=0`；
- t→t+1 收益始终来自 t 日选择的**同一具体合约**；
- 换月价格跳空不计入可交易 Alpha。

最终 execution-aligned L4 复用了上述固定原始数据，没有再次抓取或修改历史价格。

## 最终 GitHub 证据

通过的 execution-aligned L4：

- workflow run：`32567558268`；
- tested head：`351b89e73b79b4eee960980ff9a806d89b9d01cd`；
- artifact：`execution-aligned-return-target-evidence`；
- artifact id：`9474502870`；
- SHA-256：`2ab4d6a9547a21659eef2db27b4bbd3278b65557c044468d39c41c584bfea62b`；
- `target_met=true`；
- `specific_contracts=true`；
- `roll_safe=true`；
- `max_gross_leverage<=2.0`。

后续生产接线、CLI、文档和治理修改不改变冻结 Alpha/经济参数，因此不重复抓取昂贵 L4 数据；行为性代码由主 CI 验证。

## 生产边界

生产 directional 模式：

- 与 calendar-spread / Auto 模式**账户互斥**；
- 只接受冻结的 50 品种 Universe；
- 通过 CTP 实时合约目录自动选择具体主力合约；
- 旧风险先减，再允许新增目标风险；
- 所有开仓仍经过账户、保证金、可用资金、盘口宽度、深度、涨跌停距离、订单频率等 `RiskManager` 硬门；
- 使用 FAK，成交和仓位真相仍来自 Broker；
- `REDUCE_ONLY` / Kill Switch / reconciliation 继续由原生产引擎负责；
- Shadow 和 test CTP 是真实资金前置门。

历史结果使用日线 OHLC 和公开具体合约数据，不包含过去数年的完整 L1 bid/ask/depth。因此 107.46% 不是可直接兑现的实盘收益承诺。真实成交成本、部分成交、交易所时段差异和风控缩量都可能显著降低实际收益。
