# 生产上线检查表

这是**真实资金门**，不是代码完成清单。历史回测、production-mechanics proxy 和 GitHub CI 都不能代替真实 L1、测试柜台和未来未见数据。

当前代码级状态：previous-day activity、trading-day signal gate、stale activity fail-closed、reduction-first、risk-off/REDUCE_ONLY、restart reconciliation、directional execution-quality 和 production-mechanics acceptance 均已实现。

但经济结论必须分开：

- float-notional L4：Base **107.4623% 年化 / 27.4097% 最大回撤 / gross≤2x**；
- production-mechanics proxy：Base **6.7861% 年化**，2024-09-19 触发 5% daily-loss gate 后停机；Stress **3.4290% 年化**，14 个 margin reject days，最终同日触发 margin-ratio gate。

因此当前**不满足“production-equivalent 年化 ≥100%”**，也不应通过静默放宽风控来勾选该项。

## A. 历史与账户机械证据

- [x] specific-contract 不使用 continuous roll jump 作为收益。
- [x] t→t+1 收益来自 t 日已选择同一具体合约。
- [x] 20 天交割黑窗。
- [x] gross target ≤2x。
- [x] Float Base 5bp 最近两年年化 107.4623%。
- [x] Float Base 最大回撤 27.4097%。
- [x] Float Stress 15bp 年化 58.1372%。
- [x] `pristine_final_oos=false` 与历史选择偏差明确记录。
- [x] Production-mechanics proxy 使用 frozen multipliers / integer lots / contract cap。
- [x] Production-mechanics proxy 使用 margin/cash/daily-loss/high-watermark gates。
- [x] Production gap 已量化并记录 first divergence / halt date。
- [ ] **Production-mechanics 年化 ≥100%：当前不满足（Base 6.7861%）。**
- [ ] 新发生、此前未参与选择的未来数据持续验证。
- [ ] 未来显著恶化时优先降低/关闭风险，不在同一历史上无限追参。

## B. 当前生产风险门认知

- [x] 已确认 Base proxy 在 2024-09-19 触发 `daily loss limit reached`。
- [x] 已确认 Stress proxy 有 14 个 margin opening reject days。
- [x] 已确认 Stress 最终触发 `margin ratio limit reached`。
- [x] 没有为了恢复 100% 历史数字自动放宽 daily-loss / DD / margin / cash / leverage。
- [ ] 如果未来拟调整风险阈值，必须先有真实账户风险承受能力与 Shadow/test/small-capital 新证据。
- [ ] 明确理解 proxy 较小最大回撤主要来自早停机，不能当作策略天然更稳。

## C. 配置与账户隔离

- [ ] 使用冻结 50 品种 Universe，不为追近期收益随意删改。
- [ ] `directional.max_gross_leverage <= 2.0`。
- [ ] directional 与 static pairs / Auto 不同时启用。
- [ ] 同一账户没有手工/其它程序交易破坏 account-exclusive 假设。
- [ ] 目标交易所和品种权限已开通。
- [ ] `rebalance_window` 经过测试柜台验证。
- [ ] max margin、min available、daily loss、total drawdown 已按真实承受能力确认。

## D. Previous-day activity snapshot

- [ ] CTP catalog 覆盖冻结 50 品种。
- [ ] 连续观察至少一个完整 trading day，生成 `directional_activity.json`。
- [ ] snapshot volume/OI 等于上一完整交易日最后可见 activity。
- [ ] 次交易日当前累计 volume/OI 变化不会重新改变已冻结主力。
- [ ] listing/expiry/20 天过滤正确。
- [ ] OI → volume → expiry → symbol 排序与离线重建一致。
- [ ] 新部署尚无 completed snapshot 时不会新增风险。
- [ ] 重启后能恢复最近 completed snapshot。
- [ ] completed activity 比最新完整 signal day 陈旧时 fail-closed。

## E. Signal day

- [ ] 50 品种 continuous OHLC 在 Shadow 中连续多日获取成功。
- [ ] 最新 OHLC 日期覆盖 `completed_activity_snapshot.trading_day`。
- [ ] 周末/节假日由 required trading day 语义通过，而非单纯依赖 96h。
- [ ] 普通交易日漏完整 bar 时即使小时数未超限也会拒绝。
- [ ] provider 临时失败但缓存已覆盖 required day 时可继续。
- [ ] required signal 缺失且账户为空时不新增风险。
- [ ] required signal/activity 缺失且有方向仓位时进入 `REDUCE_ONLY`。

## F. Reduction-first / contract unavailable

- [ ] target=0、反转、超额风险能先 reduction。
- [ ] 某个新目标无 eligible contract 时不会阻塞其它产品减仓。
- [ ] 不可用产品已有仓位只冻结当前手数，不加仓、不换月。
- [ ] 旧合约 reductions 全部由 Broker 确认后，下一 cycle 才开新合约。
- [ ] 活动订单存在时 rebalance 等待。
- [ ] reduction FAK 未成交/partial 后下一 cycle 以 Broker 真实持仓重算。

## G. Shadow

- [ ] `afuture shadow --config config/afuture.directional-live.example.toml` 连续运行多个真实交易日。
- [ ] Shadow 真实读取 CTP catalog/tick/trading day/metadata。
- [ ] Shadow 不调用真实 CTP `send_order()`。
- [ ] 每日 signal day / activity day / selected contract / target lots 可解释。
- [ ] actual gross 与 modeled gross 差异可解释。
- [ ] depth 足以覆盖计划手数。
- [ ] stale quote/metadata/signal/activity failure 没有反复造成异常状态。
- [ ] 实际 margin/risk-off 次数与 proxy 的 divergence 有可解释差异。

## H. Doctor / 测试柜台

- [ ] `afuture doctor` 登录、account、complete position、catalog、metadata 全部通过。
- [ ] 单方向 FAK 开仓。
- [ ] FAK 未成交、partial、reject。
- [ ] 平仓与平今/平昨。
- [ ] 多产品 order-rate。
- [ ] 换月 reduction 完成前不新增风险。
- [ ] 未知 order/trade/position drift 触发安全停机。
- [ ] 断线、行情陈旧、快照陈旧状态正确。
- [ ] `REDUCE_ONLY` 只减仓。
- [ ] query/order 流控不过载。
- [ ] 实际 Broker margin/commission 与 metadata/结算单一致。

## I. Restart / state truth

- [ ] 正常退出前 StateStore 已保存最新 expected positions。
- [ ] 重启后 RuntimeState 与 Broker 完整持仓一致时 reconciled。
- [ ] 任一合约今昨/多空不一致时 fail-closed。
- [ ] directional 没有第二份独立策略仓位可与 Broker 漂移。
- [ ] Kill Switch 只有在 metadata/position/account 全部确认后解除。

## J. Account sizing

- [ ] 用真实 equity、multiplier、当前价格复核 integer target lots。
- [ ] 小资金不会因整数手数长期把组合压成极少数产品。
- [ ] `max_contract_volume` 不产生严重 target tracking error。
- [ ] 实际 margin 缩量后的 gross 可解释。
- [ ] 账户规模不足时降低风险/复杂度，不放宽硬门。
- [ ] 不通过 leverage >2x 修复收益差距。

## K. Directional execution quality

- [ ] `directional_rebalance` 持续记录 signal/activity day、target、planned turnover。
- [ ] `directional_fill` 只来自 Broker trade callback。
- [ ] expected vs fill price 的 slippage bps 正常。
- [ ] 真实 commission 与结算单抽样一致。
- [ ] `directional_cycle` 有 realized turnover、tracking error、latency、partial/reject。
- [ ] `quality-report.directional` 每日可读。
- [ ] realized turnover 没有系统性显著高于模型假设。
- [ ] p95 slippage 没有吞掉大部分可兑现 Alpha。

## L. 极小真实资金

- [ ] 从 1 手或最小合理风险开始。
- [ ] 测试规模即使完全损失也不影响整体资金安全。
- [ ] 连续多个交易日无 order/state/position 事故。
- [ ] 主力切换与换月正确。
- [ ] 实际成本与 Stress 场景差异可解释。
- [ ] 实际回撤符合账户风险预算。
- [ ] risk-off/REDUCE_ONLY 的真实退出能够完成。

## M. 扩大风险前

只有以下全部满足才考虑扩大：

- [ ] 新发生未来数据没有持续推翻 Alpha。
- [ ] Shadow 稳定。
- [ ] 测试柜台稳定。
- [ ] 极小真实仓位稳定。
- [ ] execution quality 可接受。
- [ ] integer/multiplier/margin 后组合没有严重漂移。
- [ ] 实盘回撤符合风险预算。
- [ ] 已重新评估 production proxy 与真实执行差异，而不是只引用 107.4623% float L4。

107.4623% 是已观察研究结果；6.7861% 是当前生产机械假设下的历史 proxy。两者都不是未来年度收益承诺。
