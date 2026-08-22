# 实盘、Shadow、停机与恢复

## 1. 正式模式

`afuture` 支持两种账户互斥模式：Calendar Spread / Auto 与 Execution-Aligned Directional。两者共用 Broker、`RiskManager`、Kill Switch、`REDUCE_ONLY`、StateStore、启动对账和审计链。

Directional 的 107.4623% 是存在历史选择偏差的 float-notional L4 结果，不等于真实资金已获批准。

## 2. 推荐上线顺序

```text
历史 L4 + production-mechanics proxy
→ 多交易日 CTP Shadow
→ CTP doctor
→ 测试柜台 FAK/partial/reject/reconnect
→ 极小真实仓位
→ execution-quality / 结算单核对
→ 新发生未来数据
→ 再决定是否扩大
```

## 3. 凭证

CTP 凭证只从环境变量读取：

```text
AFUTURE_CTP_USER
AFUTURE_CTP_PASSWORD
AFUTURE_CTP_BROKER
AFUTURE_CTP_APP_ID
AFUTURE_CTP_AUTH_CODE
```

真实生产还要求：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

以及 `--confirm-live`。

## 4. Directional 配置

使用 `config/afuture.directional-live.example.toml` 作为 test/Shadow 起点：

- 冻结 50 品种；
- gross target ≤2.0x；
- 20 天 expiry filter；
- `20:55-09:10` 跨午夜 rebalance window；
- account/margin/cash/depth/limit/order-rate 硬门。

宽 rebalance window 只表示“允许各品种在其第一个 fresh session 执行”，不保证所有品种同一秒成交。

## 5. Previous-day activity snapshot

生产不再在开盘后用**当前交易日累计** OI/volume 重新挑主力。

`DirectionalActivityTracker` 按 CTP `Tick.trading_day` 记录每个允许合约最后可见 activity。当 trading day 从 D 推进到下一日时：

```text
D 日最终 volume/OI
→ DirectionalActivitySnapshot
→ 原子保存 directional_activity.json
→ D+1 合约选择只读这个 completed snapshot
```

D+1 当前 Tick 仍必须 fresh，并用于 bid/ask、depth、limit、价格和实际下单；它只是不能改变已经冻结的 D 日主力选择。

全新部署尚未积累 completed snapshot 时不允许新增 directional 风险。重启可从 sidecar 恢复最近完整 snapshot。

## 6. Signal-day freshness

每天进入 rebalance 生命周期时：

```text
required_signal_day = completed activity day
连续 OHLC 最新日期必须 >= required_signal_day
```

`signal_max_age_hours=96` 只作为第二层长期停更/异常 timestamp 保护，不再靠小时数猜交易日。

- provider 临时失败，但缓存已覆盖 required day：允许使用缓存；
- required day 缺失且账户为空：拒绝新增风险；
- required day 缺失且已有 directional risk：返回 `risk_off`，进入 `REDUCE_ONLY` 并持续 flatten；
- 数据来自未来或超过小时上限：同样 fail-closed。

因此外部日线源失效不会让接近 2x gross 的旧方向仓位无限期“原地不动”。

## 7. 合约不可用时的 reduction-first

某个新目标产品没有 eligible contract/fresh quote 时，不再整体 reject 全组合。

顺序为：

1. 先读取 Broker 真实 positions；
2. 计算 target weights；
3. 找出 target=0、反转、超额等确定性 reductions；
4. 缺失的新目标只禁止该产品新增/换月；已有该产品仓位临时保持当前手数；
5. 其它 reductions 正常提交；
6. reductions 结算后下一周期才允许 openings。

这保证“新增风险不可用”不会阻塞“风险下降”。

## 8. Shadow

```bash
afuture shadow --config config/afuture.directional-live.example.toml --duration-seconds 3600
```

Directional Shadow 使用真实：

- CTP catalog/tick/trading day/metadata；
- completed activity snapshot；
- continuous OHLC signal；
- 冻结 96-template policy；
- integer target lots；
- 正式 RiskManager 和 quality 生命周期。

账户/订单/成交/持仓由本地 SimBroker 维护，`ShadowBroker.send_order()` 不调用真实 CTP 报单。

## 9. Doctor

```bash
afuture doctor --config config/afuture.directional-live.example.toml
```

Doctor 只检查登录、account/position snapshot、catalog、multiplier、price tick、margin、commission metadata，不包含真实报单入口。

## 10. 新增风险门

单合约 opening：

- fresh quote；
- session；
- bid/ask width；
- top-of-book depth；
- limit-distance；
- contract volume cap。

opening batch：

- 日亏损；
- high-watermark drawdown；
- 当前/预计 margin；
- available cash；
- contract total volume；
- order-rate limiter。

2x gross 是策略目标上限，不是放宽账户保证金门的授权。

## 11. REDUCE_ONLY

Directional 的已有方向风险需要主动退出时：

```text
RUNNING
→ REDUCE_ONLY
→ flatten/reducing FAK
→ Broker 真实持仓变为 0
→ HALTED，等待人工复核
```

账户日亏损/总回撤等场景不会直接 HALTED 后遗留 directional 风险。

## 12. 启动对账

Directional 不持久化第二份策略仓位。重启时统一执行：

1. Broker ready；
2. fresh account/complete position snapshot；
3. 处理活动订单；
4. `RuntimeState.positions` 与 Broker 完整持仓逐合约比较；
5. metadata/account gates；
6. 完全一致才允许恢复。

任何 mismatch 都 fail-closed。

## 13. Execution quality

Directional 下单时 manager 只注册 expected execution metadata；真实 order/trade callback 到达后：

- 基础 TradingEngine 先更新统一 position truth；
- quality 再记录 realized fill/slippage/commission；
- order 终态不会在 trade callback 到达前抢先结束 cycle。

`quality-report` 的 `directional` 子汇总应持续检查：

- realized turnover；
- median / p95 slippage bps；
- commission；
- target tracking error；
- completion latency；
- partial/rejected count。

## 14. Production-mechanics proxy 的边界

Proxy 已加入 multiplier、整数手数、contract cap、reduction-first、margin/cash、daily-loss/high-watermark，但仍没有多年历史真实：

- L1 bid/ask/depth；
- queue；
- partial/reject；
- CTP/交易所流控；
- 逐日 Broker margin schedule；
- 真实结算手续费。

因此它比 float-notional L4 更接近账户语义，但仍不是实盘收益承诺。

## 15. 测试柜台必须验证

- FAK 开/平；
- partial / reject；
- 平今/平昨；
- 夜盘跨 trading day activity freeze；
- 断线重连；
- metadata/query/order rate；
- 多合约 reduction-first；
- signal risk-off → REDUCE_ONLY；
- restart reconcile；
- 实际手续费。

这些通过后再进入极小真实仓位。
