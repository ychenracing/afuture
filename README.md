# afuture

`afuture` 是一套面向个人投资者的国内期货**跨期套利交易系统**。系统把研究回放、模拟撮合和 CTP 实盘接入放在同一套策略、风控与订单模型上，默认采用保守的失败关闭（fail-closed）原则。

> 期货账户并不是程序直接连到交易所撮合主机。个人投资者通常通过期货公司的 CTP 交易/行情前置接入；`afuture` 的实盘适配器基于 VeighNa 的 `vnpy_ctp`。

## 当前能力

- 同品种不同月份的跨期价差均值回归策略。
- 使用双腿最新可成交盘口构造价差，滚动 Z-score 产生开仓、回归退出和极端偏离退出信号。
- 回放和模拟交易支持一档盘口、限价单、FAK、FOK、部分成交、滑点、保证金、今昨仓和手续费。
- 上期所、能源中心平今/平昨拆单；其他交易所使用普通平仓。
- 双腿组合保证金一次性预检，并对静态保证金率增加安全缓冲。
- 最大保证金、最小可用资金、日亏损、总回撤、单合约手数、组合数量、临近到期禁开仓、行情陈旧和报单速率限制。
- 双腿非原子成交监控；超时失衡时只减仓修复，并触发持久化停机。
- CTP 行情、下单、撤单、账户、持仓和交易日适配。
- 启动持仓对账、活动订单检查、断线停机、停机状态持久化、策略状态恢复。
- JSONL 审计日志、滚动运行日志、JSON 账户/绩效报告。
- CLI 支持配置校验、历史回放、CTP 实盘和停机后的人工状态恢复入口。

## 策略边界

当前正式交易策略只实现**期货跨期套利**，这是有意收窄的范围。股指期货与 ETF 的期现/基差套利需要另一条证券交易通道，CTP 不能完成 ETF 那一腿，因此本项目不会把“只连接 CTP”包装成完整的股指期现套利。

第一阶段建议研究流动性较好的同品种跨期组合，例如豆粕、螺纹钢、PTA 等；这只是研究方向，不是实时品种推荐。合约月份、保证金、手续费、最后交易日和流动性必须在每次实盘前依据开户期货公司的最新信息更新。

## 安装

研究、测试和回放：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

需要 CTP：

```bash
python -m pip install -e ".[live,dev]"
```

实盘适配器当前针对 `vnpy 4.4.x` 与 `vnpy_ctp 6.7.11.4` 的接口行为实现并测试；`pyproject.toml` 已限制版本范围，避免上游私有回调结构变化时被无意升级。

## 快速验证

```bash
afuture validate --config config/afuture.example.toml
afuture replay --config config/afuture.example.toml --data examples/sample_ticks.csv
```

输出默认位于 `runtime/`：

- `replay_report.json`：账户快照和回测指标。
- `audit.jsonl`：信号、订单、成交和停机事件。
- `afuture.log`：运行日志。
- `replay_state.json`：可恢复的状态快照。

回放会主动清理其状态文件后重新开始，避免上一次运行污染研究结果。

## 实盘配置

先复制示例配置并修改：

1. `system.mode = "live"`。
2. `[ctp]` 中填写期货公司提供的交易前置、行情前置和 `environment = "test"` 或 `"production"`。
3. 将合约乘数、最小变动价位、保证金率、手续费改成账户真实值。
4. 每个 `pairs` 必须填写 `expiry_near` 和 `expiry_far`，内容应来自合约真实最后交易日；系统在临近到期窗口禁止新开仓。
5. 实盘组合不得复用同一个合约，避免持仓归属不清。

敏感字段不写进 TOML：

```text
AFUTURE_CTP_USER
AFUTURE_CTP_PASSWORD
AFUTURE_CTP_BROKER
AFUTURE_CTP_APP_ID      # 期货公司要求认证时填写
AFUTURE_CTP_AUTH_CODE   # 期货公司要求认证时填写
```

先使用期货公司测试环境：

```bash
afuture live --config config/live.toml
```

生产柜台额外设置：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

并显式执行：

```bash
afuture live --config config/live.toml --confirm-live
```

两道确认不能防止亏损，它们只是降低误连生产账户的概率。

## 实盘启动安全门

启动后不会立即发单。流程为：

1. CTP 行情、交易登录成功并完成合约初始化。
2. 等待 CTP 定时查询链路返回新的账户事件和一次完整持仓快照。
3. 若发现遗留活动订单，先撤单并停机。
4. 本地持久化持仓与柜台持仓逐项对账。
5. 有历史停机标记时，只有重新连接且对账通过才能解除。
6. 开始接收信号和发单。

建议使用**独立期货账户或至少保证该账户不被其他程序同时交易**。否则系统无法可靠判断某个持仓/订单是否属于自身套利组合。

## 默认风控

默认值面向约 50 万元级别的谨慎起步，不代表适用于所有账户：

| 规则 | 默认值 |
|---|---:|
| 最大保证金/权益 | 35% |
| 最小可用资金/权益 | 50% |
| 单交易日最大亏损 | 1% |
| 从权益高水位最大回撤 | 8% |
| 最大同时套利组合 | 3 |
| 单合约最大手数 | 10 |
| 静态保证金估算缓冲 | 1.20 倍 |
| 行情最大陈旧时间 | 10 秒 |
| 临近到期禁开仓 | 5 天 |
| 每分钟最大普通报单 | 20 |
| 双腿失衡允许窗口 | 2 秒 |

实盘前应按账户规模、品种波动、期货公司保证金和手续费重新设置，而不是直接照抄默认值。

## 项目结构

```text
afuture/
  broker/
    base.py          # 柜台统一接口
    sim.py           # 模拟撮合
    ctp.py           # VeighNa CTP 实盘适配
  models.py          # 统一领域模型
  strategy.py        # 跨期均值回归策略
  risk.py            # 账户和组合级风控
  execution.py       # 双腿执行与裸腿修复
  position.py        # 今昨仓和持仓簿
  engine.py          # 实时/回放统一事件链路
  state.py           # 状态持久化
  reconcile.py       # 启动对账
  data.py            # CSV Tick 数据
  fees.py            # 手续费模型
  report.py          # 绩效和账户报告
  journal.py         # 结构化审计日志
  cli.py             # 命令行入口
```

详细说明见：

- `docs/architecture.md`
- `docs/live-trading.md`
- `docs/data-and-backtest.md`

## 仍需你在真实账户完成的验证

仓库可以建立 CTP 连接并形成完整的软件交易闭环，但代码仓库本身无法替代以下账户侧验证：

- 你的期货公司 CTP 前置地址、BrokerID、AppID/AuthCode 是否正确。
- 账户是否已经开通目标交易所/品种权限。
- 期货公司实际保证金率、手续费和平今费。
- 测试柜台中的下单、撤单、断线重连、夜盘交易日和持仓对账。

因此正确上线顺序是：**历史回放 → CTP 测试环境 → 极小真实仓位 → 再扩大资金**。不要跳过测试柜台直接用 50 万元生产实盘。
