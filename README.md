# afuture

`afuture` 是一套面向个人使用的国内商品期货**同品种跨期套利自动交易系统**。目标不是建设交易平台，而是保持一条小而完整的闭环：

```text
发现具体合约 → 筛选净机会 → 风险预算 → 双腿执行 → 异常减仓 → 证据复验
```

系统基于 VeighNa `vnpy_ctp` 接入期货公司 CTP。正式策略只做同一品种不同交割月份的跨期套利，不混入方向性期货、跨品种、期现、期权或高频做市。

> **重要：** 软件闭环完整不等于已经证明高收益/低回撤。最终生产晋级以真实多年份数据的 `accept-auto`、CTP Shadow/测试柜台和极小真实仓位证据为准。

## 当前生产链

```text
CTP Contract Catalog / Historical Catalog
                  ↓
            AutoPairManager
  相邻月份 + 到期过滤 + 少量白名单
                  ↓
 volume / OI / depth / executable Z-score
 half-life / stationarity / Net Edge
                  ↓
       少量 open-eligible pairs
                  ↓
       CalendarSpreadStrategy
                  ↓
 PortfolioRiskAnalyzer + RiskManager
                  ↓
           PairExecutor
                  ↓
       CtpBroker / SimBroker
```

关键原则：

- **可成交价差**：多价差用 `near.ask - far.bid`，空价差用 `near.bid - far.ask`；退出和止损同样使用可实际平仓的方向性价差。
- **Net Edge**：开仓前扣除手续费、滑点和裸腿风险缓冲；统计偏离明显但净边际不足时不交易。
- **自动发现**：CTP 合约目录 → 品种白名单 → 到期过滤 → 相邻月份 → 活动度/均值回归/Net Edge 排名。
- **管理权与开仓权分离**：已有持仓即使失去 Auto 资格仍继续管理退出，但会立刻失去新开仓权限；平仓后立即退役。
- **动态手数**：`PairConfig.volume` 是上限，真实开仓手数由账户权益、价差波动、盘口深度和硬上限共同决定。
- **组合风险**：限制同风险组暴露和高相关价差组合。
- **异常状态机**：`RUNNING → REDUCE_ONLY → HALTED`；裸腿优先只减仓，不把“程序停机”误当作“风险已消失”。
- **真实元数据**：动态候选使用 CTP 合约乘数、price tick、保证金和手续费；慢速费率查询在后台预取，不阻塞 Tick 主循环。
- **有限 warm history**：实盘只持久化 `lookback + buffer` 级别采样行情，盘中重启不必从零等待完整 lookback。
- **状态真相**：本地期望持仓与柜台完整快照分离，未知成交/订单/持仓漂移 fail-closed。

## 安装

研究、回放和测试：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate
python -m pip install -e ".[dev]"
```

需要 CTP：

```bash
python -m pip install -e ".[live,dev]"
```

当前实盘适配器针对 `vnpy 4.4.x` 与 `vnpy_ctp 6.7.11.4` 接口行为实现。升级 CTP/VeighNa 后必须重新跑测试柜台验收。

## 研究流程

### 1. 先检查数据

```bash
afuture data-check \
  --config config/afuture.auto-replay.example.toml \
  --data path/to/multi_contract_ticks.csv
```

`data-check` 保留源 CSV 原始顺序，检查：

- 时间范围、交易日数、每日样本数；
- 合约/品种覆盖；
- 重复与乱序；
- 无效盘口；
- volume/OI 缺失比例；
- 涨跌停字段缺失；
- 日内长断档；
- 每日 Auto 是否真的有双腿候选；
- 单一品种样本是否过度集中。

### 2. 普通回放

```bash
afuture replay \
  --config config/afuture.example.toml \
  --data examples/sample_ticks.csv
```

### 3. Auto 回放

```bash
afuture replay \
  --config config/afuture.auto-replay.example.toml \
  --data examples/auto_sample_ticks.csv
```

Auto 回放和实盘使用同一个 `AutoPairManager → TradingEngine → RiskManager → PairExecutor` 生命周期，不另写一套“研究版选标器”。

### 4. 单 pair 诊断

```bash
afuture accept \
  --config config/afuture.example.toml \
  --data examples/research_ticks.csv \
  --pair m_calendar \
  --train-days 4 \
  --validation-days 2 \
  --oos-days 2 \
  --step-days 2 \
  --stress-multipliers 1.0,1.5,2.0
```

`accept` 保留用于单 pair 研究和定位 first divergence，但它**不是最终机器人的生产晋级门**。

### 5. 最终 Auto Portfolio 晋级

```bash
afuture accept-auto \
  --config config/afuture.auto-replay.example.toml \
  --data path/to/multi_year_multi_contract_ticks.csv \
  --train-days 120 \
  --validation-days 40 \
  --oos-days 40 \
  --step-days 40 \
  --stress-multipliers 1.0,1.5,2.0
```

`accept-auto` 直接运行最终 Auto Portfolio，包含：

- Train → Validation → OOS 时间隔离；
- 小型**全局**参数邻域，只调信号/候选参数，不为每个商品单独过拟合；
- OOS 不参与参数选择；
- 1x / 1.5x / 2x 成本压力；
- leave-one-product-out；
- single-product attribution；
- remove-best-OOS-period；
- top-depth haircut；
- 延迟和 market impact；
- 确定性 Tick 缺失；
- 0.5 / 1 / 2 秒双腿异步；
- 少量 volume/OI 缺失。

默认预注册门要求：

- 聚合 OOS 收益必须为正；
- 正收益 OOS fold 比例至少 60%；
- 最差 OOS 回撤不超过 6%；
- OOS 必须有足够交易样本；
- 成本压力不能严重崩塌；
- 多品种时不能出现明显单一品种垄断或 leave-one-product-out 灾难性失效。

这些阈值是研究门，不是收益保证，也不能为了看到最终 OOS 后再反向移动。

## Shadow：真实行情，不发真实单

```bash
afuture shadow --config config/live.toml
```

Shadow：

- 连接真实 CTP；
- 使用真实合约目录；
- 使用真实行情；
- 查询真实保证金/手续费元数据；
- 运行真实 Auto Selector / RiskManager / PairExecutor；
- 所有订单只进入本地保守 `SimBroker`；
- `ShadowBroker.send_order()` 从类型层面不调用真实 CTP `send_order()`。

Shadow 每次启动使用新的虚拟账户，证据写入：

```text
runtime/shadow_execution_quality.jsonl
runtime/shadow_market_samples/
runtime/shadow_audit.jsonl
```

查看汇总：

```bash
afuture quality-report --config config/live.toml --shadow
```

## Execution Quality

Live/Shadow 会记录三层证据：

1. `candidate`：pair、Z-score、平稳性、半衰期、volume/OI、depth、候选分数、预期 Net Edge、拒绝原因；
2. `decision`：交易动作、风险手数、是否允许、执行拒绝原因；
3. `round_trip`：预期/实际价差、滑点、手续费、双腿成交时间差、部分成交、rollback、REDUCE_ONLY、realized Net Edge。

Live 默认写：

```text
runtime/execution_quality.jsonl
```

汇总：

```bash
afuture quality-report --config config/live.toml
```

CTP 成交回报本身通常不携带结算后的单笔账户手续费，因此正式分析应把程序根据**实时查询费率 + 实际成交价**计算的手续费与期货公司结算单定期核对；不能把模型手续费冒充结算单真值。

## CTP Doctor

```bash
afuture doctor --config config/live.toml
```

`doctor` 只检查：

- 行情/交易登录；
- fresh account + position snapshot；
- 合约目录；
- 少量合约元数据查询；
- 当前账户/交易日。

**Doctor 永远不下单。** 真正的 FAK、部分成交、拒单、断线、平今/平昨 smoke 必须在期货公司测试柜台按生产检查表执行，不能由仓库在没有你的测试账户时伪造“已通过”。

## 实盘

复制：

```bash
cp config/afuture.live.example.toml config/live.toml
```

密钥只从环境变量读取：

```text
AFUTURE_CTP_USER
AFUTURE_CTP_PASSWORD
AFUTURE_CTP_BROKER
AFUTURE_CTP_APP_ID
AFUTURE_CTP_AUTH_CODE
```

测试柜台：

```bash
afuture live --config config/live.toml
```

生产柜台额外要求：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

并显式：

```bash
afuture live --config config/live.toml --confirm-live
```

启动顺序：CTP 就绪 → Auto 合约目录/恢复 → fresh 账户/完整持仓快照 → 元数据门 → 遗留订单门 → 本地/柜台持仓对账 → `RUNNING`。

## 默认范围与明确不做

默认只使用一个小商品白名单并只开少量组合。代码支持扩大 Universe，但当前不建议：

- `products=["*"]` 全市场扫描；
- 每个商品单独优化一套参数；
- 在线自动调参；
- 机器学习/AI 选标；
- 跨品种/跨交易所/期现/期权新策略；
- 默认开启夜盘；
- 为追求回测收益提高杠杆；
- GUI、Web 后台、数据库、Redis、Kafka、微服务。

当前最大不确定性是 **Alpha 是否真实**，不是功能不够多。

## Feature Freeze 条件

以下证据全部满足后应停止继续增加策略功能：

1. 最终 Auto Portfolio 多年份 Walk-forward/OOS 通过；
2. 2x 成本压力和数据/微观结构扰动没有明显崩塌；
3. leave-one-product-out 没有灾难性依赖；
4. Shadow 证明实时盘口中仍存在足够 Net Edge；
5. CTP 测试柜台完成订单、部分成交、断线、交易日和恢复验证；
6. 极小真实仓位连续多交易日无状态/对账/执行事故；
7. 实际手续费/滑点没有吞掉大部分预期 Edge；
8. 回撤符合预注册门。

达到后主要工作应转为观察、维护、CTP/交易所变化适配和定期 OOS 复验，而不是继续堆特征。

## 项目结构

```text
afuture/
  auto.py               # CTP 合约发现、排名、动态生命周期
  auto_runtime.py       # 非阻塞 CTP 元数据预取
  auto_research.py      # 最终 Auto Portfolio Walk-forward/OOS/Stress
  auto_acceptance.py    # 预注册 Auto 晋级门
  data_quality.py       # 研究数据质量门
  sample_store.py       # 有界 warm sampled history
  quality.py            # candidate / decision / execution quality
  strategy.py           # 跨期均值回归与结构失效
  risk.py               # 账户/市场/动态仓位风控
  portfolio_risk.py     # 相关性与风险组
  execution.py          # 双腿执行、回滚、只减仓修复
  engine.py             # 统一实时/回放事件链
  broker/
    sim.py              # 保守模拟撮合
    shadow.py           # 真实行情 + 本地模拟订单
    ctp.py              # VeighNa CTP
```

详细说明：

- [架构与数据流](docs/architecture.md)
- [数据、回放与研究](docs/data-and-backtest.md)
- [实盘、Shadow 与恢复](docs/live-trading.md)
- [生产上线检查表](docs/production-checklist.md)
