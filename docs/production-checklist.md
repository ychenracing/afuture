# 生产上线检查表

这份表是**真实资金上线门**，不是代码完成清单。没有外部账户/真实历史数据的项目 CI 不能代替其中的人工证据。

## A. 数据与 Auto Portfolio 研究

- [ ] 数据至少覆盖约两年，且包含多个品种、多个交割月份和换月。
- [ ] bid/ask/depth/volume/OI/trading_day 基本完整。
- [ ] `afuture data-check` 无 hard failure。
- [ ] 原始 CSV 无未解释乱序。
- [ ] 每个计划交易日都有真实双腿候选数据，而不是只有静态 catalog。
- [ ] 单一品种没有无意中垄断绝大多数样本。
- [ ] `afuture accept-auto` 使用最终 Auto Portfolio，而不是只看固定 pair。
- [ ] 参数只由 Train+Validation 选择，OOS 不参与调参。
- [ ] Aggregate OOS Return > 0。
- [ ] Positive OOS Fold Ratio 达到预注册门（默认至少 60%）。
- [ ] Worst OOS Drawdown 不超过预注册门（默认 6%）。
- [ ] OOS 交易样本量足够，不是十几笔交易支撑结论。
- [ ] 1.5x / 2x cost stress 没有出现不可接受崩塌。
- [ ] Leave-one-product-out 没有灾难性失效。
- [ ] Single-product attribution 没有明显历史赢家垄断。
- [ ] Remove-best-period 后仍可接受。
- [ ] Depth haircut / latency / market impact stress 可接受。
- [ ] Data-gap / quote-skew / volume-OI-missing stress 可接受。

## B. Shadow Observation

- [ ] `afuture shadow` 已连续运行多个真实交易日。
- [ ] Shadow 使用真实 CTP contract catalog。
- [ ] Shadow 使用真实账户 margin/commission metadata。
- [ ] 已人工确认 Shadow 没有真实订单/真实成交。
- [ ] `shadow_execution_quality.jsonl` 有 candidate / decision / round_trip 证据。
- [ ] 实时盘口中的 candidate 数量与历史研究假设大体一致。
- [ ] 大多数可交易机会的实际 bid/ask 没有把 Net Edge 完全吃掉。
- [ ] Shadow modeled slippage 在可接受范围。
- [ ] Shadow 没有频繁 metadata timeout / stale quote / cross-leg skew 停机。

## C. CTP Doctor

- [ ] `afuture doctor` 登录通过。
- [ ] Fresh account snapshot 到达。
- [ ] Fresh complete position snapshot 到达。
- [ ] Contract catalog 非空且品种/月份合理。
- [ ] 少量目标合约 multiplier / price tick 查询正确。
- [ ] margin / commission query 返回合理。
- [ ] Doctor 输出 `orders_sent = 0`。

## D. 测试柜台真实订单验证

这些项目不能由 GitHub CI 模拟成“已通过”。

- [ ] 测试柜台 FAK 开仓报单成功。
- [ ] 撤单成功。
- [ ] 部分成交场景已验证。
- [ ] 第二腿拒单/失败后第一腿撤单/回滚行为已验证。
- [ ] 裸腿能进入 `REDUCE_ONLY`。
- [ ] REDUCE_ONLY 只减仓，不会继续新增风险。
- [ ] 平今/平昨拆单与柜台一致。
- [ ] 未知活动订单触发停机。
- [ ] 未知成交触发停机。
- [ ] 持仓漂移触发停机。
- [ ] 断线触发停机。
- [ ] 快照陈旧触发停机。
- [ ] 新交易日 margin/commission cache 重新刷新。
- [ ] 动态 pair 重启恢复后仍能正确管理原仓位。
- [ ] CTP query/order rate 没有触发期货公司异常限制。

## E. 自动发现生命周期

- [ ] `auto.products` 只包含愿意真实交易且账户已开通的品种。
- [ ] 不使用 `products=["*"]` 作为第一阶段生产配置。
- [ ] 到期过滤得到的月份符合真实最后交易日。
- [ ] 新进入前排月份会自动订阅。
- [ ] 已持仓 pair 排名下降不会被强制轮换平仓。
- [ ] 已持仓但失去 hard gate 的 pair 立刻失去**新开仓**权限。
- [ ] 该 pair 平仓后立即退役，不等待下一次 scan 再决定。
- [ ] Warm sampled history 在重启后恢复。
- [ ] 正常 Tick 主循环不会同步等待 CTP margin/commission query。

## F. 风险配置

- [ ] 最大保证金率按真实账户重新设置。
- [ ] 最小可用资金率按真实账户重新设置。
- [ ] 日亏损门已接受并理解。
- [ ] 总回撤 Kill Switch 已接受并理解。
- [ ] 单 pair risk budget 已按真实品种波动校准。
- [ ] `max_active_pairs` 第一阶段保持 1。
- [ ] 单合约手数第一阶段保持极小。
- [ ] 只日盘启动；夜盘没有证据前不开启。
- [ ] bid/ask、depth、limit-distance、cross-leg skew 门在目标品种上合理。

## G. Execution Quality

- [ ] Live `execution_quality.jsonl` 正常写入。
- [ ] candidate reject reason 可解释。
- [ ] modeled vs realized entry spread 已核对。
- [ ] median slippage 不高于预算。
- [ ] p95 slippage 有明确上界。
- [ ] realized commission 已与期货公司结算单抽样核对。
- [ ] realized Net Edge 没有长期明显低于 expected Net Edge。
- [ ] rollback 次数可接受。
- [ ] REDUCE_ONLY 次数接近 0，任何一次都有人复盘。

## H. 极小真实资金

- [ ] 第一阶段只交易 1 个 active pair。
- [ ] 每腿从 1 手或交易所允许的最小风险规模开始。
- [ ] 连续多个交易日无状态/持仓/对账事故。
- [ ] 实际手续费和滑点与 Shadow 预期同量级。
- [ ] 没有因为手工交易或其他程序造成外部持仓漂移。
- [ ] 扩大前重新执行一次 quality report 和账户风险复核。

## I. Feature Freeze

只有以下全部满足后才扩大资金；同时停止继续添加策略功能：

- [ ] 多年份 Auto OOS 稳定。
- [ ] Robustness 稳定。
- [ ] Shadow 稳定。
- [ ] 测试柜台稳定。
- [ ] 极小真实仓位稳定。
- [ ] Execution Quality 没有吞掉大部分 Alpha。
- [ ] 实盘回撤符合预注册风险门。

达到 Freeze 后，不再为了提高历史收益增加 ML、AI 选标、新策略类型、全市场 Universe 或在线调参。后续以维护、真实数据复验和 CTP/交易所变化适配为主。
