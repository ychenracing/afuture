# 实盘、Shadow、停机与恢复

## 1. 上线顺序

不要把“代码能连接 CTP”理解为“策略已经证明可盈利”。推荐顺序固定为：

```text
多年份 data-check
→ accept-auto
→ Shadow
→ CTP doctor
→ CTP 测试柜台订单/异常验证
→ 极小真实仓位
→ 多交易日 execution-quality 核对
→ 再决定是否扩大风险预算
```

## 2. 密钥

CTP 账号只从环境变量读取：

```text
AFUTURE_CTP_USER
AFUTURE_CTP_PASSWORD
AFUTURE_CTP_BROKER
AFUTURE_CTP_APP_ID
AFUTURE_CTP_AUTH_CODE
```

生产柜台还要求：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

并显式传 `--confirm-live`。该确认同样适用于生产柜台的 Shadow/Doctor，因为它们仍会访问真实账户和行情，虽然不会发单。

## 3. Doctor：只检查，不下单

```bash
afuture doctor --config config/live.toml
```

检查：

- CTP 行情/交易登录；
- fresh account event；
- fresh complete position snapshot；
- contract catalog；
- 少量候选合约的 multiplier/price tick/margin/commission metadata。

输出包含：

```text
ready
trading_day
account_equity
position_count
contract_catalog_count
metadata_symbols
orders_sent = 0
```

`doctor` 没有下单入口，不要把测试订单逻辑塞进 doctor。

## 4. Shadow：真实市场，虚拟账户

```bash
afuture shadow --config config/live.toml
```

Shadow 使用：

- 真实 CTP contract catalog；
- 真实 Tick；
- 真实 trading day；
- 真实 margin/commission query；
- 正式 AutoPairManager；
- 正式 TradingEngine / RiskManager / PairExecutor。

但账户、订单、持仓和成交由本地保守 SimBroker 维护。`ShadowBroker.send_order()` 不调用真实 CTP `send_order()`。

每次 Shadow 会话从新的虚拟账户开始，避免上次模拟持仓与新行情错配。Shadow sampled history 和执行证据与 Live 分开：

```text
runtime/shadow_market_samples/
runtime/shadow_execution_quality.jsonl
runtime/shadow_audit.jsonl
runtime/shadow_state.json
```

可限制一次观察时长：

```bash
afuture shadow --config config/live.toml --duration-seconds 3600
```

汇总：

```bash
afuture quality-report --config config/live.toml --shadow
```

## 5. 实盘启动安全门

```bash
afuture live --config config/live.toml
```

顺序：

1. 创建 CTP 会话；
2. 等待交易/行情登录和合约初始化；
3. Auto 读取 CTP 合约目录，恢复仍需管理的动态 pair；
4. warm-load 最近 sampled history；
5. 等待 ready 之后新产生的账户事件和完整持仓快照；
6. 静态 pair 比较本地/CTP 元数据；动态 pair 从 CTP 取真实元数据；
7. 遗留活动订单存在则撤单并停机；
8. 本地期望持仓与柜台完整快照逐合约对账；
9. Kill Switch 只有本会话元数据、账户风险和对账全部通过才允许解除；
10. 进入 `RUNNING`。

固定 `sleep` 不能替代 snapshot generation marker。

## 6. Auto 元数据查询不阻塞 Tick

恢复已有动态仓位时可以在启动安全门同步查询元数据。

正常运行中，候选接近开仓时：

```text
statistical prefilter
→ background metadata request
→ current candidate skipped
→ next scan consumes cache
```

CTP margin/commission query 不在 Tick 主循环中等待。交易日变化会清空缓存并重新刷新。

## 7. Warm History

Live 只保存桶化后的最近样本：

```text
runtime/market_samples/
```

上限约为 `lookback * 4` 级别，不保存长期原始 Tick。重启后新候选可以从最近 sampled history 恢复，不必完全从零等待。

## 8. Auto 退役

必须区分：

```text
managed
open-eligible
```

组合有仓位时即使 ranking/hard gate 失效，仍保持 `managed=true`，保证原策略能退出；同时立刻 `open-eligible=false`，不能在退出后的下一次 scan 前重新开仓。

平仓且无活动订单后立即 unregister。

## 9. 行情健康

实盘使用墙钟，因此能够发现：

- 单腿延迟；
- 双腿时间差超限；
- 两腿同时停止推送；
- 行情 timestamp 未来漂移。

仅在组合配置的活跃 session 中执行陈旧行情门，并保留启动初始化宽限。

## 10. REDUCE_ONLY

跨合约订单不是原子操作。出现双腿失衡或紧急退出失败时：

```text
RUNNING → REDUCE_ONLY
```

REDUCE_ONLY：

- 撤活动订单；
- 禁止新开仓；
- FAK 只减仓；
- 每轮继续审计；
- 风险消失后转 `HALTED`；
- 必须人工复核后才能恢复。

封板/无流动性时，`HALTED` 不代表裸腿已消失。

## 11. Execution Quality

Live：

```text
runtime/execution_quality.jsonl
```

三类事件：

### candidate

- pair；
- zscore；
- stationarity；
- half-life；
- volume/OI；
- depth；
- candidate score；
- expected Net Edge；
- reject reason。

### decision

- signal action；
- risk size；
- accepted/rejected；
- executor reject reason。

### round_trip

- expected spread；
- entry/exit realized spread；
- slippage；
- commission；
- leg latency；
- partial fill；
- rollback；
- REDUCE_ONLY；
- realized edge。

```bash
afuture quality-report --config config/live.toml
```

CTP trade callback 通常不直接提供结算单意义上的单笔实际手续费。系统质量证据应使用**实际成交价 + 已实时查询的账户费率**估算，并定期与期货公司结算单核对。差异持续明显时，应修正费用模型或停止该品种，而不是调高风险预算掩盖偏差。

## 12. 人工恢复

只有已经通过期货公司终端核验真实仓位时：

```text
AFUTURE_RECOVERY_ACK=I_VERIFIED_CTP_POSITIONS
```

```bash
afuture recover-state \
  --config config/live.toml \
  --confirm-adopt-state
```

生产环境同时需要 `--confirm-live`。

恢复只接受：

- 静态配置 pair 或状态中已持久化的动态 pair；
- 双腿等量反向；
- 手数不超过 pair cap；
- 无活动订单。

恢复后 Kill Switch **不解除**，`metadata_verified` 清空；下一次 `live` 必须重新连接、重新查元数据并独立对账。

## 13. 测试柜台必须人工/真实完成的项

仓库没有你的 BrokerID、账号、前置地址和权限，因此以下结果不能由 CI 伪造：

- 实际登录；
- FAK 下单/撤单；
- 部分成交；
- 拒单；
- 平今/平昨；
- 断线重连；
- 夜盘跨交易日；
- 期货公司查询/报单流控差异；
- 实际结算手续费。

这些必须按 `production-checklist.md` 在测试柜台留下证据后再进入极小真实资金。
