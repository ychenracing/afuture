# 实盘、Shadow、停机与恢复

## 1. 正式模式

`afuture` 支持两种账户互斥模式：Calendar Spread / Auto 与 Execution-Aligned Directional。两者共用 Broker、`RiskManager`、Kill Switch、`REDUCE_ONLY`、StateStore、启动对账和审计链。

Directional 必须同时理解两套证据：

- float-notional L4：selection-biased 最近两年 Base 年化 **107.4623%**；
- production-mechanics proxy：当前账户机械和风险门下 Base 年化约 **6.7861%**，2024-09-19 触发 5% 日亏损门后停机。

所以当前代码级生产接线完成不等于 100% 收益已经 production-equivalent，更不等于真实资金已获批准。

## 2. 推荐上线顺序

```text
历史 L4 + production-mechanics evidence
→ 多交易日 CTP Shadow
→ CTP doctor
→ 测试柜台 FAK/partial/reject/reconnect
→ 极小真实仓位
→ execution-quality / 结算单核对
→ 新发生未来数据
→ 再决定是否扩大或调整风险
```

当前最重要的验证问题不是“还能否把历史回测调得更高”，而是：真实 margin、slippage、commission、risk-off 是否支持比 proxy 更有效、同时仍安全的风险暴露。

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
- gross target 上限 2.0x；
- 20 天 expiry filter；
- `20:55-09:10` 跨午夜 rebalance window；
- max margin 35%；
- min available 25%；
- daily loss 5%；
- total drawdown 30%；
- account/depth/limit/order-rate 硬门。

2.0x 是**允许的策略 target 上限**，不是承诺生产账户会持续保持 2.0x。Production proxy 已经证明现有风险门会显著改变实际风险路径。

## 5. Previous-day activity snapshot

生产不在开盘后用当前交易日累计 OI/volume 重新挑主力。

`DirectionalActivityTracker` 按 CTP `Tick.trading_day` 记录每个允许合约最后可见 activity。trading day 从 D 推进时：

```text
D 日最终 volume/OI
→ DirectionalActivitySnapshot
→ 原子保存 directional_activity.json
→ D+1 选约只读 completed snapshot
```

D+1 当前 Tick 仍用于 fresh quote、bid/ask、depth、limit、价格和下单，但不能改变 D 已冻结的主力。

全新部署无 completed snapshot 时不新增风险。重启可恢复 snapshot，但如果 OHLC 已证明存在比它更新的完整交易日，陈旧 snapshot 不能继续用于开仓。

## 6. Signal-day freshness

```text
required_signal_day = completed activity day
continuous OHLC latest day >= required_signal_day
```

`signal_max_age_hours=96` 只是第二层长期停更/异常 timestamp 保护。

- provider 临时失败，但缓存已覆盖 required day：允许缓存；
- required day 缺失且账户为空：拒绝新增风险；
- required day 缺失且已有 directional risk：`risk_off → REDUCE_ONLY → flatten`；
- completed activity 明显落后于已完成 signal day：fail-closed；
- 数据来自未来或超过小时上限：fail-closed。

因此外部日线源或 activity sidecar 异常不会让接近 2x gross 的旧仓无限期无管理地保留。

## 7. 合约不可用时 reduction-first

某个新目标产品没有 eligible contract/fresh quote 时不整体 reject 全组合：

1. 读取 Broker 真实 positions；
2. 计算 target weights；
3. target=0、反转、超额等确定性 reductions 先执行；
4. 缺失新目标只禁止该产品新增/换月；已有该产品仓位临时保持当前手数；
5. 其它 reductions 继续；
6. reductions 经 Broker 确认后的下一 cycle 才允许 openings。

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
- 正式 RiskManager；
- directional execution-quality 生命周期。

账户/订单/成交/持仓由本地 SimBroker 维护，Shadow 不调用真实 CTP `send_order()`。

### Shadow 必须重点回答

Production proxy 已发现巨大 research/live gap，因此 Shadow 不应只检查“程序不崩”。至少要持续记录：

- target gross vs actual gross；
- target lots vs actual lots；
- margin ratio / available ratio；
- daily loss / high-watermark drawdown；
- planned vs realized turnover；
- median/p95 slippage；
- commission；
- margin/reject/risk-off 次数；
- 主力切换是否与 previous-day activity 一致。

如果 Shadow 经常在目标建立前就触发 margin/risk gates，说明当前生产风险路径本身与 107.4623% L4 不同，而不是简单的“成交滑点问题”。

## 9. Doctor

```bash
afuture doctor --config config/afuture.directional-live.example.toml
```

Doctor 只检查登录、account/position snapshot、catalog、multiplier、price tick、margin、commission metadata，不包含真实报单入口。

## 10. 新增风险门

单合约 opening：fresh quote、session、bid/ask width、top-of-book depth、limit-distance、contract volume cap。

Opening batch：日亏损、high-watermark drawdown、当前/预计 margin、available cash、contract total volume、order-rate limiter。

Production proxy 最近两年：

- Base：2024-09-19 `daily loss limit reached`；
- Stress：14 个 margin reject days，最终 2024-09-19 `margin ratio limit reached`。

这些不是应自动绕过的“回测障碍”，而是当前生产权限的真实组成部分。

## 11. REDUCE_ONLY

```text
RUNNING
→ REDUCE_ONLY
→ flatten/reducing FAK
→ Broker 真实持仓为 0
→ HALTED，等待人工复核
```

账户日亏损/总回撤/关键 signal failure 等需要退出已有方向风险时，不会直接 HALTED 后遗留 directional 风险。

## 12. 启动对账

Directional 不持久化第二份策略仓位。重启：

1. Broker ready；
2. fresh account/complete position snapshot；
3. 处理活动订单；
4. `RuntimeState.positions` 与 Broker 完整持仓逐合约比较；
5. metadata/account gates；
6. 完全一致才恢复。

任何 mismatch 都 fail-closed。

## 13. Execution quality

下单时 manager 只注册 expected metadata；真实 order/trade callback 到达后：

- 基础 TradingEngine 先更新统一 position truth；
- quality 再记录 realized fill/slippage/commission；
- order 终态不会在 trade callback 到达前抢先结束 cycle。

持续检查 `quality-report.directional`：realized turnover、median/p95 slippage、commission、tracking error、completion latency、partial/rejected count。

## 14. Production proxy 的边界

当前 production proxy 比 float L4 更接近账户语义，但仍没有多年历史真实：

- L1 bid/ask/depth；
- queue；
- partial/reject；
- CTP/交易所流控；
- 逐日 Broker margin schedule；
- 真实结算手续费；
- reduction 成交确认后的下一 cycle opening 价格。

日线 proxy 在一个交易日内只能用 open/close 近似阶段执行，因此 **6.7861% 也不是未来真实收益预测**。

## 15. 风险参数调整原则

不要因为 production proxy 低于 100% 就直接放宽 daily-loss、drawdown、margin、cash reserve 或 leverage。任何调整必须来自：

- 实际账户风险承受能力；
- 多日 Shadow；
- 测试柜台真实 margin/fee/fill；
- 极小资金 realized drawdown；
- 新发生未见数据。

目标是提高**可兑现净收益/风险比**，不是强制让历史 production proxy 回到某个预设数字。

## 16. 测试柜台必须验证

- FAK 开/平；
- partial / reject；
- 平今/平昨；
- 夜盘跨 trading day activity freeze；
- 断线重连；
- metadata/query/order rate；
- 多合约 reduction-first；
- signal/activity risk-off → REDUCE_ONLY；
- restart reconcile；
- 实际手续费和 margin。

这些通过后再进入极小真实仓位。
