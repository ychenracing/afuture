# 实盘、Shadow、停机与恢复

## 1. 当前正式模式

`afuture` 现在支持两种账户互斥的正式策略模式：

- Calendar Spread / Auto：同品种相邻月份跨期套利；
- Execution-Aligned Directional：冻结 50 品种的高收益方向组合。

配置加载阶段禁止 directional 与 static pairs/Auto 同时启用。两种模式共用 Broker 账户真相、`RiskManager`、Kill Switch、`REDUCE_ONLY`、状态持久化和 CTP 安全门。

代码级接线完成不等于真实资金已获批准。Directional 的 107.46% 是有明确选择偏差的历史 L4 结果；真实资金仍应按 Shadow → 测试柜台 → 极小仓位推进。

## 2. 推荐上线顺序

```text
历史 L4 / 配置验证
→ 多交易日 Shadow
→ CTP doctor
→ 测试柜台 FAK / 部分成交 / 拒单 / 断线验证
→ 极小真实仓位
→ execution-quality / 结算单核对
→ 新发生未来数据复验
→ 再决定是否扩大风险
```

Directional 不应因为历史收益高就跳过任何执行门。

## 3. CTP 凭证与真实资金确认

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

以及命令行 `--confirm-live`。

## 4. Directional 配置

以：

```text
config/afuture.directional-live.example.toml
```

作为 test/Shadow 起点。该文件固定：

- 50 品种 Universe；
- gross target ≤2.0x；
- 20 天合约到期过滤；
- `20:55-09:10` 跨午夜 rebalance window；
- 账户/保证金/现金/深度/涨跌停/订单频率硬门。

`20:55-09:10` 是为了允许各品种在其第一个 fresh market session 完成日度调仓，不表示所有品种都能在同一秒成交。因此 historical next-open L4 仍是执行代理，不是对未来实盘成交时点的保证。

## 5. Doctor：只检查，不下单

```bash
afuture doctor --config config/afuture.directional-live.example.toml
```

或 Calendar 配置的等价命令。

Doctor 检查：

- CTP 行情/交易登录；
- fresh account event；
- fresh complete position snapshot；
- contract catalog；
- multiplier / price tick / margin / commission metadata。

Doctor 不包含报单入口。

## 6. Shadow：真实市场，虚拟账户

```bash
afuture shadow \
  --config config/afuture.directional-live.example.toml \
  --duration-seconds 3600
```

Directional Shadow 使用：

- 真实 CTP catalog；
- 真实 Tick / trading day；
- 真实合约 metadata；
- 真实公开连续 OHLC 信号源；
- 正式冻结 directional policy；
- 正式当前合约选择、整数手数、风险门和调仓生命周期；
- 本地 SimBroker 维护虚拟 account/order/trade/position。

`ShadowBroker.send_order()` 不调用真实 CTP `send_order()`。

## 7. Directional 日度信号刷新

生产每天首次进入有效 rebalance window 时加载冻结 50 品种的连续 OHLC 历史。

安全边界：

- 数据不足 140 个交易日 → 拒绝本次新风险；
- 信号时间来自未来 → 拒绝；
- 数据超过 `signal_max_age_hours` → 拒绝；
- 任一必需品种信号源获取失败 → 本次信号不可用，不新增风险。

“拒绝本次新风险”不等于自动清空已有仓位。已有持仓仍由 Broker 真相、全局风险门、人工 Kill Switch 和 `REDUCE_ONLY` 管理；不要因外部日线源暂时失败而制造无计划强平。

## 8. Directional 当前合约选择

政策先产生产品权重，随后使用 CTP 当前目录和实时 Tick 选择具体合约：

1. 产品/交易所允许；
2. 已挂牌；
3. 距到期不少于配置阈值；
4. volume / Open Interest 达标；
5. OI 优先、volume 次优选择主合约。

缺少 eligible concrete contract 时，本周期对该目标 fail closed，不用旧的连续合约价格直接下单。

## 9. 减仓先于加仓

换月或目标变化时：

```text
当前 Broker 持仓
→ 计算 target lots
→ 如果有 reductions：只发 reducing FAK
→ 等待 Broker 回报
→ 下一周期重新读取真实持仓
→ 无 reductions 后才允许 openings
```

因此不会在同一个调仓阶段同时放大新风险并假定旧风险已经消失。

## 10. 新增风险门

Directional 每个新开合约都要通过：

- fresh quote；
- rebalance session；
- bid/ask spread；
- top-of-book depth；
- limit-distance；
- 单合约 volume cap。

整个 opening batch 再通过：

- 日亏损；
- 总回撤高水位；
- 当前/预计保证金；
- 可用资金；
- 单合约总持仓上限；
- order-rate limiter。

研究中的 2x gross 不会绕过这些门，生产目标可能被实际账户条件显著缩小。

## 11. REDUCE_ONLY 与停机

严重账户/行情/执行异常继续使用基础 TradingEngine 状态机：

```text
RUNNING
→ REDUCE_ONLY（需要继续退出风险）
→ HALTED
```

Directional 在 `REDUCE_ONLY` 中只调用 flatten/reducing 路径，不允许新增目标风险。风险消失后系统停机等待人工复核。

## 12. 启动对账

真实 `live` 仍需要：

1. CTP ready；
2. fresh account snapshot；
3. fresh complete position snapshot；
4. 处理遗留活动订单；
5. 本地状态与柜台持仓对账；
6. Kill Switch/metadata/account gate 通过；
7. 才进入 `RUNNING`。

Directional manager 不维护第二份“策略持仓真相”，每次都读取 Broker 持仓计算差额。

## 13. 历史结果和实盘差异

最终 L4 已处理 specific-contract、换月和 next-open 时点，但仍没有多年历史完整 L1：

- bid/ask；
- depth；
- queue position；
- partial fill；
- reject；
- CTP/交易所流控；
- 真实结算手续费。

而且 L4 使用目标 notional 权重，不是某个账户逐日按真实 multiplier、整数手数和保证金完全重建的资金曲线。因此 107.46% 年化不能直接视为可兑现实盘收益。

## 14. 测试柜台必须验证

GitHub CI 不能伪造以下证据：

- FAK 开仓/平仓；
- 部分成交；
- 拒单；
- 平今/平昨；
- 断线重连；
- 夜盘跨交易日；
- 合约 metadata 查询；
- query/order rate；
- directional 多合约调仓；
- 实际手续费。

这些通过后才进入极小真实仓位。

## 15. 实盘扩仓原则

第一次真实资金不要直接使用研究中 2x gross 的完整目标。先用最小手数和严格 `max_contract_volume` 证明：

- 实际成交方向与 target 一致；
- 换月行为正确；
- modeled vs realized cost 可接受；
- 真实回撤没有明显超出预期；
- 外部持仓/手工交易不会破坏 account-exclusive 假设。

只有新的真实执行证据支持后，才逐步扩大。