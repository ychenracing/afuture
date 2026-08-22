# afuture 最终研究证据结论

本文件记录 2026-08-21 至 2026-08-22 的真实数据研究收口结果。它区分“工程能力已经完成”和“经济 Alpha 已通过生产晋级门”，避免把可运行系统误写成已证明高收益策略。

## 1. 结论

- afuture 已具备 CTP 合约发现、自动候选筛选、风险预算、双腿下单、异常只减仓、状态恢复、Shadow 与执行质量证据链。
- 过去约四年的公开真实历史数据被用于分层研究；2024-08-21 至 2026-08-20 为最近两年窗口。
- 用户目标“年化收益 100% 以上且低回撤”**没有被真实、因果、成本后证据证明**。
- 因此仓库不通过放大杠杆、移动 OOS 门槛、增加每品种参数或继续在已观察 OOS 上调参来制造目标数字。
- 当前 live 示例继续属于 test/Shadow 级模板，不代表某个 Alpha 已获得生产放大授权。

## 2. 原同品种跨期 M/OI 规则

修正交易日、同步采样、front-3 Universe、交割黑窗和可见成交量后，旧的“已验证 M/OI 生产 Alpha”结论被推翻：

- prior-forward：4 笔，约 -1.958R；
- Final OOS：2 笔，约 +0.296R；
- 最近两年：5 笔，约 +1.028R；
- 16 个局部邻域：0 个通过；
- 2% 单笔风险资本代理年化约 1.07%。

该规则保留为可重复研究/Shadow 候选，不作为盈利承诺。

## 3. 参考项目迁移研究

研究了 rolling-panda-san/notebooks 的商品期限结构、basis/momentum/reversal，pairs-trading-egarch 的经济关系过滤、滚动残差、持久性和波动 regime，以及 slow-momentum-fast-reversion 的慢趋势/快反转思想。

### 3.1 broad cross-sectional L3

约 50 个中国商品期货主连、16 个预注册 momentum / slow-fast / reversal / negative-skew 配置，没有配置同时通过独立 prior 子窗口与 Train+Validation 稳定门，因此不晋级。

### 3.2 economic-pair broad L3

经济关系固定后，滚动 beta、残差 Z-score、相关门、OU 半衰期与波动 regime 出现一簇可重复正结果。把资金只分给当前最强合格 pair 后，连续主连 L3 的最佳稳定邻域仍远低于 100% 年化，因此只能进入具体合约复验，不能直接用于实盘。

### 3.3 specific-contract roll-safe L4

具体合约日线复验只使用当日可见合约，20 天交割黑窗，并保证 t 到 t+1 的收益来自同一个具体合约，不把换月价差拼成收益。

六条预注册经济关系为 P/Y、PP/V、AL/ZN、BU/FU、CU/AL、J/JM。最终只有两个 profile 完成 pre-OOS 资格，当前资格主要收敛到 BU/FU；prior 还包含 PP/V。

在 30bp 单边压力成本、最多一个 pair、gross leverage 上限 2x 下，已验证结果约为：

- 最近两年年化：4.20%；
- 最近两年最大回撤：12.88%；
- Final OOS 年化：5.78%；
- Final OOS 最大回撤：11.78%；
- `alpha_survives_specific_contract = true`；
- `target_met = false`。

这证明连续主连结果不是单纯由换月拼接制造，但它仍只是**研究 Alpha**。当前生产 Auto 执行器只处理同品种相邻月份；BU/FU、PP/V 没有完成真实 L1 Shadow、跨品种生产执行和测试柜台证据，因此不能称为生产策略。

### 3.4 60 分钟 intraday 实验

对 BU/FU 和 PP/V 使用前一交易日 OI 选择当天具体合约、完全相同的日盘 60 分钟时间戳、bar t 决策赚取 t→t+1、日内强制退出且不拼换月价格。24 个预注册 profile 中 **0 个**通过 pre-OOS 门，因此实验终止，代码不进入长期维护面。

### 3.5 多腿结构套利

进一步检查 soybean crush、steel margin、coal/steel、polymer 和 base-metals 多腿结构。固定经济配比的有限预注册网格没有形成可同时通过 prior1/prior2/train/validation 的家族；滚动多变量残差中仅 `CU ~ AL + ZN` 出现低收益邻域，收益不足以抵偿新增三腿执行复杂度。因此结构套利实验不生产化，也不长期保留实验脚本。

## 4. 为什么不继续在同一历史上调参

截至本轮结束，Final OOS 日期窗口已经被多轮研究观察，不再是 pristine holdout。继续扩大参数、关系、特征或杠杆搜索会把“没有达到 100%”转化为典型的历史过拟合，而不是新增经济证据。

后续真正能改变结论的证据只有：

1. 新发生、此前未见的交易日；
2. 真实 CTP L1 Shadow 的 bid/ask/depth 与手续费/滑点；
3. 测试柜台的部分成交、拒单、断线、平今平昨和恢复证据；
4. 极小真实仓位的持续 Net Edge 与实际回撤。

## 5. 最终治理边界

- `research-2y`、`research-broad`、`research-specific-pairs` 属于昂贵 milestone 证据门，不作为每次小改动的 CI 内循环。
- 失败且没有生产晋级价值的 intraday/structural 实验代码在最终 review 中移除，结果保留在本文档。
- 普通工程正确性由主 CI 负责；真实历史研究结论不替代 Shadow/测试柜台/实盘成交证据。
- “100% 年化”保留为目标，不写成已实现事实。
