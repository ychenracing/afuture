# 生产上线检查表

这份表是**真实资金上线门**，不是代码完成清单。GitHub CI 和历史回测不能代替真实 L1、测试柜台和未来未见数据。

> 当前状态：Execution-Aligned Directional 已完成代码级生产接线，并在允许有限历史过拟合的 specific-contract / next-open L4 上达到最近两年 **107.46% 年化 / 27.41% 最大回撤 / gross ≤2x**。但 Final OOS 已观察且为负，真实 L1/测试柜台/小资金证据尚未完成。因此“历史收益目标已达到”不等于“可以直接按研究风险满仓实盘”。

## A. 历史证据与选择偏差

- [x] specific-contract 数据不使用连续合约换月跳空作为收益。
- [x] t→t+1 收益来自 t 日已选同一具体合约。
- [x] 20 天交割黑窗。
- [x] gross target ≤2.0x。
- [x] 5bp base 最近两年年化 ≥100%。
- [x] base 最近两年最大回撤 <30%。
- [x] 15bp stress 最近两年仍为正。
- [x] 明确记录 `pristine_final_oos=false`。
- [x] 明确记录模板池存在历史选择偏差。
- [ ] 新发生、此前未参与任何选择的未来数据继续验证方向和回撤。
- [ ] 如果未来结果持续显著恶化，降低或关闭 directional，而不是继续在同一历史上追参数。

## B. Directional 配置冻结

- [ ] 使用 `config/afuture.directional-live.example.toml` 的 50 品种 Universe，未擅自删改品种来追最近收益。
- [ ] `directional.max_gross_leverage <= 2.0`。
- [ ] directional 与 static pairs / Auto 不同时启用。
- [ ] 没有其他程序或手工交易与 directional 共用同一账户，或已建立可靠的外部持仓隔离流程。
- [ ] `signal_max_age_hours` 能覆盖正常周末/节假日，同时能发现真正陈旧数据。
- [ ] 目标交易所和品种均已开通交易权限。
- [ ] 夜盘/日盘实际时段与 `rebalance_window` 经过测试柜台验证。

## C. Directional 信号源

- [ ] 50 个连续 OHLC 品种在 Shadow 中连续多日获取成功。
- [ ] 信号数据日期与中国交易日语义人工抽查一致。
- [ ] 周末/节假日没有被误判未来数据或异常陈旧。
- [ ] 单个/多个信号源失败时不会产生新风险。
- [ ] 信号恢复后不会因为缺口生成明显异常权重。
- [ ] meta 轮动与离线冻结 policy 输出做过同日对比。

## D. CTP Contract Selector

- [ ] CTP catalog 覆盖冻结 50 品种。
- [ ] 实时 OI/volume 能为当前目标品种选出合约。
- [ ] 距到期过滤符合实际最后交易日规则。
- [ ] 选出的 symbol multiplier / price tick / margin 正确。
- [ ] 合约换月日先减旧风险，再在下一周期开新合约。
- [ ] 无 eligible contract 时 fail closed，不以连续合约代码报单。
- [ ] 低流动性品种的 volume/OI 门按真实盘口重新审查。

## E. Shadow Observation

- [ ] `afuture shadow --config config/afuture.directional-live.example.toml` 连续运行多个真实交易日。
- [ ] Shadow 使用真实 CTP catalog / tick / metadata。
- [ ] 已人工确认 Shadow 不调用真实 CTP send_order。
- [ ] 每日 target products / selected contracts / target lots 可解释。
- [ ] 实际盘口 depth 能覆盖计划手数。
- [ ] 模拟 bid/ask/slippage 没有吞掉大部分历史 Alpha。
- [ ] 没有频繁 stale quote、metadata、signal feed 导致的运行中断。
- [ ] 换月和目标反转没有出现同周期先加后减风险。

## F. CTP Doctor

- [ ] `afuture doctor` 登录通过。
- [ ] Fresh account snapshot 到达。
- [ ] Fresh complete position snapshot 到达。
- [ ] Contract catalog 非空。
- [ ] 目标合约 multiplier / price tick 正确。
- [ ] margin / commission query 合理。
- [ ] Doctor 输出/人工确认没有任何报单。

## G. 测试柜台真实订单

以下项目不能由 CI 伪造：

- [ ] 单方向 FAK 开仓成功。
- [ ] FAK 未成交/部分成交状态正确。
- [ ] 平仓成功，平今/平昨与柜台一致。
- [ ] 多产品 opening batch 的真实 order-rate 合理。
- [ ] 任一开仓拒单后系统下一周期按 Broker 真实持仓重新计算目标。
- [ ] 换月 reduction 完成前不会开新合约风险。
- [ ] 活动订单存在时 rebalance 等待。
- [ ] 未知订单/成交/持仓漂移触发安全停机。
- [ ] 断线、行情陈旧、快照陈旧触发正确状态。
- [ ] `REDUCE_ONLY` 只减仓。
- [ ] 交易所/期货公司查询与报单流控没有被程序打爆。

## H. Directional 风险参数

- [ ] `max_margin_ratio` 按真实账户校准，不能因为研究 gross=2x 就放宽账户保证金门。
- [ ] `min_available_ratio` 留有足够现金缓冲。
- [ ] `max_daily_loss_ratio` 已接受并理解。
- [ ] `max_total_drawdown_ratio` 不高于愿意实际承受的账户回撤。
- [ ] 第一阶段 `max_contract_volume` 显著低于研究上限，优先从 1 手或最小合理手数开始。
- [ ] top-of-book depth multiple 足够保守。
- [ ] bid/ask ticks 和 limit-distance 在目标品种上合理。
- [ ] 没有通过提高 leverage 超过 2x 来修复实盘收益差距。

## I. Integer Lots / Account Sizing Gap

历史 L4 是目标 notional 权重，不是某个真实账户完整逐日整数手数资金曲线。上线前必须：

- [ ] 用真实账户 equity、每个合约 multiplier 和当前价格检查目标手数。
- [ ] 小资金账户不会因整数手数导致单品种权重严重失真。
- [ ] 单合约 cap 不会让实际组合长期只剩一两个品种。
- [ ] 保证金门缩量后的实际 gross 与预期一致。
- [ ] 如果账户规模不足以复制多品种权重，先降低目标复杂度/风险，而不是放宽风控。

## J. Calendar / Auto 模式（若单独启用）

Directional 与 Calendar/Auto 账户互斥。若另用独立账户运行原套利模式：

- [ ] `afuture data-check` 无 hard failure。
- [ ] `accept-auto` 使用最终 Auto Portfolio。
- [ ] Auto 到期/front-3/adjacent-month 生命周期正确。
- [ ] warm history 正常恢复。
- [ ] metadata prefetch 不阻塞 Tick。
- [ ] managed 与 open-eligible 分离。
- [ ] 双腿部分成交/回滚/REDUCE_ONLY 在测试柜台验证。

旧 corrected M/OI 本身未通过高收益经济门，不应因为 directional 达标而被错误提升风险。

## K. Execution Quality 与结算单

- [ ] Live 审计正常写入。
- [ ] 每日 target weight → target lot → actual position 可追溯。
- [ ] 实际成交价相对决策盘口滑点可统计。
- [ ] 真实手续费与期货公司结算单抽样一致。
- [ ] 真实 rebalance turnover 没有明显高于历史假设。
- [ ] 真实 gross、保证金、可用资金变化可解释。
- [ ] REDUCE_ONLY / rollback / reject 次数可接受并逐次复盘。

## L. 极小真实资金

- [ ] 第一阶段只用愿意完全损失也不影响整体资金的测试规模。
- [ ] 单合约从 1 手或最小合理风险开始。
- [ ] 连续多个交易日无状态/订单/持仓事故。
- [ ] 换月真实行为正确。
- [ ] 实际交易成本没有明显超过 15bp 压力场景所隐含的容忍度。
- [ ] 实际回撤在预注册缩小风险门内。
- [ ] 扩大资金前重新做账户级 stress 和 quality review。

## M. 扩大风险前的最终门

只有以下全部满足后才考虑扩大：

- [ ] 新发生未来数据没有持续推翻 Alpha。
- [ ] Shadow 稳定。
- [ ] 测试柜台稳定。
- [ ] 极小真实仓位稳定。
- [ ] 真实 execution cost 可接受。
- [ ] Integer lot / multiplier / margin 后的实际组合没有严重漂移。
- [ ] 实盘回撤符合账户风险预算。

达到上述门后，以真实证据逐步调整风险；不要把 107.46% 历史年化当成必须在每个未来年度兑现的固定指标。