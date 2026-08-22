# 生产上线检查表

这是**真实资金门**，不是代码完成清单。历史回测、production-mechanics proxy 和 GitHub CI 都不能代替真实 L1、测试柜台和未来未见数据。

当前代码级状态：Execution-Aligned Directional 已完成 previous-day activity、trading-day signal gate、reduction-first、risk-off/REDUCE_ONLY、restart reconciliation 和 directional execution-quality 闭环。历史 float-notional L4 在选择偏差口径下为 **107.4623% 年化 / 27.4097% 最大回撤 / gross≤2x**；Final OOS 已观察且为负。

## A. 历史与账户机械证据

- [x] specific-contract 不使用 continuous roll jump 作为收益。
- [x] t→t+1 收益来自 t 日已选择同一具体合约。
- [x] 20 天交割黑窗。
- [x] gross target ≤2x。
- [x] Base 5bp 最近两年年化 107.4623%。
- [x] Base 最大回撤 27.4097%。
- [x] Stress 15bp 年化 58.1372%。
- [x] `pristine_final_oos=false` 与历史选择偏差明确记录。
- [x] production-mechanics proxy 代码加入 multiplier、integer lots、contract cap、margin/cash、daily-loss/high-watermark gates。
- [ ] 新发生、此前未参与选择的未来数据持续验证。
- [ ] 未来显著恶化时优先降低/关闭风险，不在同一历史上无限追参。

## B. 配置与账户隔离

- [ ] 使用冻结 50 品种 Universe，不为追近期收益随意删改。
- [ ] `directional.max_gross_leverage <= 2.0`。
- [ ] directional 与 static pairs / Auto 不同时启用。
- [ ] 同一账户没有手工/其它程序交易破坏 account-exclusive 假设。
- [ ] 目标交易所和品种权限已开通。
- [ ] `rebalance_window` 经过测试柜台验证。
- [ ] `max_margin_ratio`、`min_available_ratio`、daily loss、total drawdown 已按真实承受能力确认。

## C. Previous-day activity snapshot

- [ ] CTP catalog 覆盖冻结 50 品种。
- [ ] 连续观察至少一个完整 trading day，生成 `directional_activity.json`。
- [ ] 人工抽查 snapshot 中 volume/OI 等于上一完整交易日最后可见 activity。
- [ ] 次交易日开盘后当前累计 volume/OI 变化不会重新改变已冻结主力。
- [ ] listing/expiry/20 天过滤正确。
- [ ] OI → volume → expiry → symbol 排序与离线重建一致。
- [ ] 新部署尚无 completed snapshot 时不会新增风险。
- [ ] 重启后能恢复最近 completed snapshot。

## D. Signal day

- [ ] 50 品种 continuous OHLC 在 Shadow 中连续多日获取成功。
- [ ] 最新 OHLC 日期覆盖 `completed_activity_snapshot.trading_day`。
- [ ] 周末/节假日由 required trading day 语义通过，而不是单纯依赖 96h。
- [ ] 普通交易日漏掉完整 bar 时即使小时数未超限也会拒绝。
- [ ] provider 临时失败但缓存已覆盖 required day 时可继续。
- [ ] required signal 缺失且账户为空时不新增风险。
- [ ] required signal 缺失且有方向仓位时进入 `REDUCE_ONLY`。

## E. Reduction-first / contract unavailable

- [ ] target=0、反转、超额风险能先 reduction。
- [ ] 某个新目标无 eligible contract 时不会阻塞其它产品减仓。
- [ ] 不可用产品已有仓位只冻结当前手数，不加仓、不换月。
- [ ] 旧合约 reductions 全部由 Broker 确认后，下一周期才开新合约。
- [ ] 活动订单存在时 rebalance 等待。
- [ ] reduction FAK 未成交/部分成交后下一周期以 Broker 真实持仓重新计算。

## F. Shadow

- [ ] `afuture shadow --config config/afuture.directional-live.example.toml` 连续运行多个真实交易日。
- [ ] Shadow 真实读取 CTP catalog/tick/trading day/metadata。
- [ ] Shadow 不调用真实 CTP `send_order()`。
- [ ] 每日 signal day / activity day / selected contract / target lots 可解释。
- [ ] actual gross 与 modeled gross 差异可解释。
- [ ] depth 足以覆盖计划手数。
- [ ] stale quote/metadata/signal failure 没有反复造成异常状态。

## G. Doctor / 测试柜台

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

## H. Restart / state truth

- [ ] 正常退出前 StateStore 已保存最新 expected positions。
- [ ] 重启后 RuntimeState 与 Broker 完整持仓一致时 reconciled。
- [ ] 任一合约今昨/多空不一致时 fail-closed。
- [ ] directional 没有第二份独立策略仓位可与 Broker 漂移。
- [ ] Kill Switch 只有在 metadata/position/account 全部确认后解除。

## I. Account sizing

- [ ] 用真实 equity、multiplier、当前价格复核整数 target lots。
- [ ] 小资金不会因整数手数长期把组合压成极少数产品。
- [ ] `max_contract_volume` 不产生严重 target tracking error。
- [ ] 实际 margin 缩量后的 gross 可解释。
- [ ] 账户规模不足时降低风险/复杂度，不放宽风控硬门。
- [ ] 不通过 leverage >2x 修复收益差距。

## J. Directional execution quality

- [ ] `directional_rebalance` 持续记录 signal/activity day、target、planned turnover。
- [ ] `directional_fill` 只来自 Broker trade callback。
- [ ] expected vs fill price 的 slippage bps 正常。
- [ ] 真实 commission 与结算单抽样一致。
- [ ] `directional_cycle` 有 realized turnover、tracking error、latency、partial/reject。
- [ ] `quality-report.directional` 每日可读。
- [ ] realized turnover 没有系统性显著高于历史假设。
- [ ] p95 slippage 没有吞掉大部分 Alpha。

## K. 极小真实资金

- [ ] 从 1 手或最小合理风险开始。
- [ ] 测试规模即使完全损失也不影响整体资金安全。
- [ ] 连续多个交易日无 order/state/position 事故。
- [ ] 主力切换与换月正确。
- [ ] 实际成本没有明显超过 Stress 场景容忍度。
- [ ] 实际回撤符合账户风险预算。

## L. 扩大风险前

只有以下全部满足才考虑扩大：

- [ ] 新发生未来数据没有持续推翻 Alpha。
- [ ] Shadow 稳定。
- [ ] 测试柜台稳定。
- [ ] 极小真实仓位稳定。
- [ ] execution quality 可接受。
- [ ] integer/multiplier/margin 后组合没有严重漂移。
- [ ] 实盘回撤符合风险预算。

107.4623% 是已观察历史结果，不是必须在每个未来年度兑现的固定指标。
