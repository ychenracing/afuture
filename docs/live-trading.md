# CTP 实盘使用说明

## 1. 准备条件

向开户期货公司确认：

- CTP 交易前置地址和行情前置地址；
- BrokerID；
- 用户名和密码；
- 是否需要 AppID/AuthCode；
- 测试环境和生产环境是否使用不同前置；
- 目标品种交易权限；
- 当前保证金、手续费、平今规则和最后交易日。

程序通过期货公司 CTP 前置进入期货交易链路，不是绕过期货公司直接访问交易所撮合系统。

## 2. 先跑测试柜台

复制 `config/afuture.example.toml`，修改为 `mode = "live"` 并增加：

```toml
[ctp]
environment = "test"
td_address = "期货公司提供的交易前置"
md_address = "期货公司提供的行情前置"
```

每个套利组合还必须填入真实最后交易日：

```toml
expiry_near = "实际日期"
expiry_far = "实际日期"
```

然后通过系统环境变量注入账号。不要把密码、授权码提交到 GitHub。

## 3. 启动顺序

```bash
afuture validate --config config/live.toml
afuture live --config config/live.toml
```

测试柜台至少验证：

- 登录和行情订阅；
- 开仓两腿都成交；
- 一腿拒单或无成交时的回滚；
- 撤单；
- 上期所/能源中心今昨仓平仓；
- 手动断网后持久化停机；
- 重启后持仓一致时恢复；
- 重启后持仓不一致时拒绝恢复；
- 夜盘交易日切换。

## 4. 生产柜台

生产配置必须使用 `environment = "production"`。此外还需要同时满足：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

以及：

```bash
afuture live --config config/live.toml --confirm-live
```

这只是防误操作机制，不是风险保证。

## 5. 实盘账户要求

优先使用独立账户。不要让手工交易、其他程序和 `afuture` 同时操作相同合约，否则持仓归属和启动对账会变得不可靠。

初次生产实盘只使用能够承受完全损失的小仓位。确认多个交易日的成交、手续费、夜盘、断线和日终状态都正常之后，再逐步扩大资金。

## 6. 持久化停机后的恢复

持仓漂移、未知成交、未知活动订单、账户风险越限、行情/账户快照失效等情况会触发持久化停机。不要直接删除 `runtime/state.json` 或手工把 `kill_switch` 改成 `false`，否则会绕过本地期望持仓与柜台真实持仓的因果对账。

如果确认柜台持仓是你希望系统继续管理的正确套利持仓，可以执行受控恢复：

```text
AFUTURE_RECOVERY_ACK=I_VERIFIED_CTP_POSITIONS
```

测试环境：

```bash
afuture recover-state --config config/live.toml --confirm-adopt-state
```

生产环境还必须同时满足生产确认门：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

```bash
afuture recover-state --config config/live.toml --confirm-live --confirm-adopt-state
```

恢复命令只会在以下条件同时满足时采纳柜台持仓：

- 当前本地状态已经处于停机；
- CTP 重新登录成功；
- 账户事件和完整持仓快照都在本次恢复连接后重新到达；
- 没有遗留活动委托；
- 柜台持仓只包含配置中的合约；
- 每个非空组合都恰好是配置手数的完整、方向相反双腿。

即使恢复成功，停机开关仍保持开启。随后必须重新运行 `afuture live`，让程序在一个新的连接会话中再次独立对账；第二次对账通过后才允许解除停机。

## 7. 停机时的活动委托

触发停机后，系统先发送撤单，并在断开 CTP 前继续短暂处理撤单和成交回报。`afuture live` 的 `--halt-drain` 参数控制这一收尾窗口，默认 3 秒。

如果网络已经断开或窗口结束时仍存在活动委托，本地停机状态不会解除。下一次启动仍会先检查活动委托并再次撤单，不会直接恢复策略发单。
