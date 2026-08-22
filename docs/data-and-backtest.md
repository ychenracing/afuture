# 数据、回放与研究

## 1. 数据层级

### Tick / 生产级输入

Calendar/Auto 的标准 Tick CSV：

```text
timestamp,symbol,exchange,bid_price,ask_price,last_price,bid_volume,ask_volume,trading_day
```

Auto 研究还应包含：

```text
limit_up,limit_down,volume,open_interest
```

`timestamp` 必须带时区，中国期货交易时段统一按 `Asia/Shanghai` 解释。

### Directional 信号与 L4

Directional 使用两类数据，职责不同：

- **连续日线 OHLC**：只生成产品信号和历史 meta score；
- **specific-contract OHLC / OI / volume**：用于真实换月语义和 next-open 执行验证。

公开源没有过去数年的完整 L1 bid/ask/depth，因此历史结果不能替代 CTP Shadow、测试柜台和真实成交证据。

## 2. 因果规则

任何正式研究至少满足：

- t 日尚未完成的信息不能决定 t 日已经发生的收益；
- continuous roll jump 不得计入可交易收益；
- historical Universe 只能包含当时已挂牌合约；
- specific-contract t→t+1 收益来自 t 日已选择的同一个具体合约；
- Final OOS 若被调参过程观察过，必须标记 non-pristine。

Directional 最终执行口径：

```text
截至 t 收盘的历史 → t+1 目标权重
旧权重 × (t close → t+1 open)
+ 新权重 × (t+1 open → t+1 close)
- t+1 open 调仓 turnover cost
```

## 3. Calendar / Auto Replay

```text
AutoPairManager
→ TradingEngine
→ CalendarSpreadStrategy
→ PortfolioRisk / RiskManager
→ PairExecutor
→ SimBroker
```

历史行情健康、裸腿超时和普通报单限速使用事件时间；SimBroker 旧单在当前 Tick 触发成交时，trade/order 回报先于同 Tick 的新策略决策。

`afuture data-check` 负责乱序、断档、活动度、合约覆盖和 point-in-time Universe 基础门。

## 4. corrected M/OI 历史专项

`.github/workflows/research-2y.yml` 保留为手动 milestone。修正交易日、完全同步时间戳、front-3、20 天黑窗、历史 listing 和 visible volume 后，旧同品种 M/OI 结论为：

```text
prior-forward             4 trades, -1.958R
final OOS                  2 trades, +0.296R
recent two years           5 trades, +1.028R
neighbor stability         0 / 16
2% risk proxy annualized   ≈ 1.07%
```

该结论没有被新的 directional 结果改写；它仍说明 M/OI calendar Alpha 本身没有达到高收益门。

## 5. 收益目标研究路线

为追求年化 100%，研究从市场中性套利扩展到低 gross leverage 多品种方向组合，覆盖：

- cross-sectional relative momentum；
- time-series momentum；
- moving-average trend；
- breakout；
- short-horizon reversal；
- acceleration / slow-fast；
- economic residual mean reversion；
- 多腿结构关系和 intraday 实验。

失败的 intraday/structural 实验不进入生产维护面。

## 6. Broad L3

约 50 个成熟中国商品期货主连用于 family 搜索。市场中性多 Alpha 组合即使 2x gross 也没有接近目标；进一步允许方向性产品暴露并以收益优先选择后，连续合约 L3 曾达到约 123% 年化。

该 L3 结果不能直接晋级，因为连续合约和 close-to-close 假设会高估真实换月/执行表现，所以必须进入 specific-contract L4。

## 7. Specific-contract L4

最终固定原始 L4 数据由 GitHub Actions 抓取：

```text
products                 = 50
candidate contract calls = 3000
usable concrete contracts= 2540
specific daily rows      ≈ 495086
missing next returns     = 0 on final products
```

合约规则：

- point-in-time OI/volume；
- 20 天交割黑窗；
- 只使用同一具体合约的下一交易日价格；
- 不拼连续合约换月跳空；
- gross notional ≤2x。

## 8. Execution-aligned 最终策略

最终生产研究参数固定：

- Universe：50 品种，按代码字母序；
- template pool：specific-contract next-open 历史选择后的 96 个模板；
- family：breakout / tsmom / momentum / moving-average / reversal / acceleration；
- meta lookback：10；
- meta rebalance：5；
- active templates：3；
- meta score source：已完成的 continuous `open→close` intraday proxy；
- base cost：5bp one-way；
- stress cost：15bp one-way；
- extreme cost：30bp one-way；
- max gross：2.0x。

2024-08-21~2026-08-20 specific-contract / next-open：

| 指标 | Base | Stress |
|---|---:|---:|
| 年化 | **107.46%** | **58.14%** |
| 累计 | **317.54%** | **142.94%** |
| 最大回撤 | **27.41%** | **32.96%** |
| Sharpe | **1.69** | **1.22** |

30bp extreme 最近两年约 9.96% 年化、46.76% 最大回撤。

## 9. 选择偏差与 Final OOS

用户允许有限过拟合，因此最终模板池在已经观察过的最近两年 specific-contract 执行历史上做过收益优先筛选。报告必须同时记录：

```text
selection_bias_acknowledged = true
pristine_final_oos          = false
```

已观察 Final OOS（2026-02-21~2026-08-20）：

```text
base annualized   ≈ -10.73%
base max drawdown ≈ 24.98%
stress annualized ≈ -31.42%
```

因此 107.46% 只能称为“允许有限过拟合后达到的最近两年历史目标”，不能称为独立泛化证明。

## 10. L4 与真实账户之间仍有差异

L4 已修复连续合约和执行时点，但仍不是完整实盘复制：

- 没有多年历史 L1 bid/ask/depth；
- 没有部分成交、拒单和真实订单排队；
- 研究按目标 notional 权重计算，不是针对某一账户逐日做整数手数、乘数、保证金和最小变动价位的完整资金曲线；
- 生产 `RiskManager` 可能因账户保证金、深度、限价或单合约手数缩小目标。

所以历史收益上限高于真实可兑现收益的风险必须通过 Shadow/测试柜台/小资金检验。

## 11. 验证层级

```text
L1  单测 / 因果 / gross / roll 回归
L2  策略与 runtime smoke
L3  broad family search
L4  specific-contract / next-open execution-aware 经济门
Final 主 CI + code review
```

昂贵 L3/L4 不在每个小改动后重复。只有策略公式、成本、合约选择、执行时点或数据方法发生实质变化才重跑。

## 12. 后续新增证据

当前最有价值的新信息不是继续扩大历史参数空间，而是：

1. 新发生的未来交易日；
2. 多交易日 CTP Shadow；
3. 测试柜台的真实 FAK、拒单、部分成交、断线与恢复；
4. 小资金实际滑点、手续费和回撤；
5. 如果能够获得，可靠的历史 L1 数据。

最终收益证据详见 [`return-target-100-evidence.md`](return-target-100-evidence.md)。