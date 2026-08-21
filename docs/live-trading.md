# 实盘、停机与恢复

## 上线前提

先完成历史研究和 CTP 测试柜台验证，再考虑真实资金。不要把“程序能连接 CTP”理解为“策略已经证明可盈利”。

## 密钥和配置

真实账号信息只放环境变量，不写进仓库：

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

并显式传 `--confirm-live`。

## 启动流程

`afuture live` 依次执行：

1. 创建 CTP 行情/交易会话并订阅配置合约。
2. 等待交易、行情登录和合约初始化。
3. 在“已就绪”之后等待新的账户事件和一次完整持仓查询快照。
4. 查询实时合约保证金/手续费并执行保守性校验。
5. 检查是否存在遗留活动订单；存在则撤单并停机。
6. 本地期望持仓与柜台完整持仓对账。
7. 已有 Kill Switch 时，只有本次元数据校验、账户风险和对账全部通过才清除。
8. 进入事件循环。

固定 `sleep` 不能替代完整快照边界，因此代码使用账户/持仓 generation marker 判断是否真的收到一轮新快照。

## 行情健康时钟

实盘使用墙钟持续计算最新 Tick 的年龄，所以不仅能发现单腿落后，也能发现两腿同时停止推送的行情整体冻结。组合配置了交易时段时，仅在对应活跃时段执行该行情陈旧检查；开盘初始订阅保留一个短暂的行情初始化窗口。

## REDUCE_ONLY

CTP 跨合约报单不是原子交易。出现一腿成交、一腿失败时，系统不会继续开风险仓位，而会进入：

```text
REDUCE_ONLY
```

行为：

- 撤销当前活动订单；
- 禁止新增风险；
- 使用 FAK 只减仓修复裸腿；
- 每轮事件继续检查风险；
- 仓位重新平衡/清空后转 `HALTED`，要求人工复核。

如果市场封板或无流动性，`HALTED` 不代表裸腿已经消失。应根据本地告警和期货公司终端立即人工处理。

## 人工恢复状态

只有确认状态文件与真实账户脱节，并且你已经通过期货公司终端人工核验仓位时，才使用：

```text
AFUTURE_RECOVERY_ACK=I_VERIFIED_CTP_POSITIONS
```

```bash
afuture recover-state \
  --config config/live.toml \
  --confirm-adopt-state
```

生产柜台还需要 `--confirm-live`。

恢复规则：

- 只接受配置中的合约；
- 每个套利组合必须双腿等量、方向相反；
- 实际手数可以小于配置 `volume`（动态仓位），但不能超过它；
- 有活动订单时不接纳状态；
- 恢复后 Kill Switch **仍保持**；
- `metadata_verified` 被清空；
- 下一次 `live` 必须重新连接、重新查询元数据并完成第二次独立对账。

## CTP 元数据限制

当前模型能表达按成交额和按手手续费，以及按成交额比例保证金。若 CTP 返回当前模型不能可靠映射的固定金额保证金，系统会拒绝通过元数据门，而不是用猜测值继续运行。

## 告警

关键事件会写入 `runtime/alerts.jsonl`。配置 `alert.webhook` 后还会向通用 Webhook POST JSON。Webhook 失败不会阻塞核心交易线程，但本地告警仍保留。

应至少关注：

- `REDUCE_ONLY`；
- Kill Switch / HALTED；
- 持仓漂移；
- 未知成交或未知活动订单；
- CTP 断线/快照陈旧；
- 元数据校验失败；
- 日亏损、总回撤和保证金风险。
