# afuture 研究证据总览

本文件保留 2026-08-21~2026-08-22 的研究脉络，并说明后续收益目标计划如何改变全局结论。旧的套利专项结果没有被删除或改写；新的 directional 结果是另一条账户互斥策略链。

## 1. 全局结论

研究分成两个阶段：

1. **套利优先阶段**：corrected M/OI、经济 pair、BU/FU/PP/V、intraday、结构套利等均没有接近 100% 年化；
2. **收益优先阶段**：在用户允许有限历史过拟合、但 gross leverage 不超过 2x 的前提下，引入多品种 directional 组合，并在 specific-contract / next-open 历史口径上达到最近两年 **107.46% 年化 / 27.41% 最大回撤**。

因此：

- “旧套利 Alpha 没有达到 100%”仍然正确；
- “afuture 全项目从未达到 100%”已经被后续研究取代；
- 最终高收益结果具有明确选择偏差，Final OOS 已观察且为负，不能写成独立泛化证明。

最新收益证据以 [`return-target-100-evidence.md`](return-target-100-evidence.md) 为准。

## 2. corrected M/OI 同品种跨期

修正中国期货交易日、同步采样、front-3 Universe、交割黑窗、历史 listing 和当时可见成交量后：

- prior-forward：4 笔，约 -1.958R；
- Final OOS：2 笔，约 +0.296R；
- 最近两年：5 笔，约 +1.028R；
- 16 个局部邻域：0 个通过；
- 2% 单笔风险资本代理年化约 1.07%。

因此旧“M/OI 已验证生产高收益 Alpha”结论作废。该结果与 directional 策略无关。

## 3. 参考策略思想的套利研究

研究吸收了三类思想：

- 商品期限结构、basis / momentum / reversal；
- 经济关系过滤、滚动残差、持久性、半衰期、volatility regime；
- slow momentum / fast reversion。

没有复制第三方交易代码，也没有为历史数字引入重型不可解释框架。

### 3.1 Broad cross-sectional / market-neutral

约 50 个中国商品期货主连上的 momentum / slow-fast / reversal / skewness 等市场中性配置，没有形成足够稳定且高收益的家族。

### 3.2 Economic pair L3

固定经济关系后，滚动 beta、残差 Z-score、相关门、OU 半衰期与波动 regime 出现过一簇正结果，但收益仍远低于 100%。

### 3.3 Specific-contract pair L4

六条重点关系：P/Y、PP/V、AL/ZN、BU/FU、CU/AL、J/JM。

在真实具体合约、20 天交割黑窗、同合约 t→t+1、30bp 单边压力成本、最多 1 个 pair、gross≤2x 下，最终主要收敛到 BU/FU：

```text
recent annualized       ≈ 4.20%
recent max drawdown     ≈ 12.88%
final OOS annualized    ≈ 5.78%
final OOS max drawdown  ≈ 11.78%
alpha_survives_specific_contract = true
target_met = false
```

它证明 BU/FU 研究信号不是单纯换月拼接假象，但没有成为新的 directional 高收益策略来源。

### 3.4 Intraday 与结构套利

- BU/FU + PP/V 60 分钟 intraday：24 个预注册 profile，0 个通过 pre-OOS；
- soybean crush、steel/coke margin、polymer/base-metals 等多腿结构：没有形成收益/稳定性足够的家族；
- 失败实验不进入长期生产维护面。

## 4. 收益优先 directional 阶段

纯套利收益与 100% 目标相差一个数量级后，研究空间扩展到 directionally exposed commodity portfolio，同时保留：

- 因果信号；
- 具体合约换月；
- explicit cost；
- gross leverage ≤2x；
- 账户/订单/成交状态机不绕过。

研究 family 包括：

- breakout；
- time-series momentum；
- momentum；
- moving-average trend；
- reversal；
- acceleration / slow-fast。

连续合约 L3 首先发现可达 100% 的高收益组合，但 specific-contract next-open 初版下降明显，说明理论 PnL 与真实执行目标错位。随后把模板筛选和 meta score 对齐到 execution-aware 历史口径，并明确承认选择偏差。

## 5. 最终 execution-aligned L4

固定原始数据：

- 50 品种；
- 3,000 候选合约请求；
- 2,540 可用具体合约；
- 约 495,086 行日线；
- point-in-time OI/volume；
- 20 天交割黑窗；
- final products `missing_next_contract_returns=0`。

冻结策略：

- 96-template pool；
- meta lookback=10；
- meta rebalance=5；
- meta count=3；
- continuous intraday proxy 只用于已完成历史的 meta ranking；
- gross≤2x。

2024-08-21~2026-08-20：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.46%** | **58.14%** |
| 累计收益 | **317.54%** | **142.94%** |
| 最大回撤 | **27.41%** | **32.96%** |
| Sharpe | **1.69** | **1.22** |

30bp extreme 最近两年约 9.96% 年化。

该阶段正式满足用户要求的“历史年化 100%”目标，但只能在下面的选择偏差限定下成立。

## 6. 选择偏差与反证

用户明确允许有限过拟合，因此模板池使用已经观察过的最近两年 specific-contract 执行历史做收益优先选择。

必须同时记录：

```text
selection_bias_acknowledged = true
pristine_final_oos          = false
```

已观察 Final OOS（2026-02-21~2026-08-20）：

- base 年化约 -10.73%；
- base 最大回撤约 24.98%；
- stress 年化约 -31.42%。

所以该策略具有强烈行情依赖/选择偏差风险。107.46% 不是未来收益承诺。

## 7. 生产晋级状态

与旧研究不同，execution-aligned directional 已经完成**代码级生产接线**：

- 与 Calendar/Auto 账户互斥；
- 冻结 50 品种 Universe 和生产 policy；
- CTP 当前合约自动选择；
- 整数手数目标；
- 先减旧风险，再开新风险；
- fresh quote / depth / spread / limit-distance；
- 账户/保证金/现金/单合约上限；
- FAK；
- Broker 持仓真相；
- `REDUCE_ONLY` / Kill Switch / startup reconciliation。

但“代码级 production path”不等于“真实资金已批准”。还需要 Shadow、测试柜台、极小真实仓位和未来数据。

## 8. 历史 L4 的剩余模型差异

即使 specific-contract / next-open 已通过，仍没有：

- 多年完整 L1 bid/ask/depth；
- queue/partial fill/reject；
- 真实 CTP 流控；
- 逐日真实结算费率；
- 针对某一个账户的 multiplier + integer lot + margin 完整资金曲线。

因此真实收益可能显著低于历史目标值。

## 9. 后续研究治理

接下来高信息量证据优先级：

1. 新发生未来交易日；
2. 多交易日 CTP Shadow；
3. 测试柜台真实订单；
4. 极小资金 realized execution；
5. 更可靠历史 L1 数据。

没有新证据时，不继续用更高杠杆或无限参数空间追历史数字。