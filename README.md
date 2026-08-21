# afuture

`afuture` 是一套面向个人投资者的国内商品期货**同品种跨期套利交易系统**。它把历史研究、保守模拟撮合和 CTP 实盘接入放在同一套策略、风控、执行和状态模型上，核心原则是：**先证明净交易边际，再允许开仓；任何状态不确定时优先减少风险。**

> 个人期货账户通常通过期货公司的 CTP 交易/行情前置接入交易所，并不是程序直接连接交易所撮合主机。项目的实盘适配器基于 VeighNa `vnpy_ctp`。

## 当前范围

正式策略只做同一品种不同交割月份的跨期套利，例如豆粕近月/远月。项目**不**把股指期现套利、跨品种套利、高频做市或方向性期货交易混入当前生产链路。

核心能力：

- **可成交价差**：多价差使用 `near.ask - far.bid`，空价差使用 `near.bid - far.ask`，不再只看 mid-price。
- **Net Edge**：开仓前扣除手续费、滑点和裸腿风险缓冲；Z-score 异常但净边际不足时不交易。
- **状态化均值回归**：滚动 Z-score、Entry-anchored stop、最长持有、结构性均值漂移/波动率突变退出。
- **动态手数**：`volume` 是最大允许手数，实际手数由账户风险预算、价差波动、盘口深度和硬上限共同决定。
- **市场微观结构保护**：盘口宽度、一档深度、涨跌停距离、开盘冷静期、收盘禁开仓窗口、到期日黑名单。
- **组合风险**：滚动价差变化相关性和 `risk_group` 集中度限制。
- **双腿异常恢复**：`RUNNING → REDUCE_ONLY → HALTED`；裸腿时持续只减仓修复，风险消除后仍要求人工复核才能恢复正常交易。
- **自动发现/自动交易**：从 CTP 合约目录生成同品种相邻月份，实时按成交量、Open Interest、盘口深度、均值回归、Z-score 和 Net Edge 排名，只激活少量最佳组合并交给现有策略/风控/执行链自动下单。
- **CTP 实盘**：行情、下单、撤单、账户、持仓、订单/成交回报、完整持仓快照、交易日处理。
- **CTP 元数据安全门**：启动时查询合约乘数、price tick、保证金率和账户手续费；本地配置可以更保守，但不能低估柜台真实风险参数。
- **启动与运行对账**：未知成交、未知活动订单、持仓漂移、CTP 断线或快照陈旧都会触发 fail-closed。
- **状态完整性**：状态文件带 schema version、单调 sequence、SHA-256 checksum、最后订单/成交 ID，并兼容旧状态迁移。
- **研究工具**：Scanner 计算成交量、Open Interest、深度、Z-score、半衰期、平稳性代理和 Net Edge；`accept` 执行 Train/Validation/OOS/成本压力 Walk-forward。
- **保守模拟**：一档深度消耗、部分成交、FAK/FOK、滑点、延迟和 market impact。
- **审计与告警**：JSONL 审计日志、本地关键告警文件和可选通用 Webhook。

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

实盘适配器针对 `vnpy 4.4.x` 与 `vnpy_ctp 6.7.11.4` 的当前接口行为实现。上游版本变化后必须重新跑测试柜台验收，不能直接升级生产环境依赖。

## 快速开始

校验研究配置：

```bash
afuture validate --config config/afuture.example.toml
```

保守历史回放：

```bash
afuture replay \
  --config config/afuture.example.toml \
  --data examples/sample_ticks.csv
```

自动选标历史回放：

```bash
afuture replay \
  --config config/afuture.auto-replay.example.toml \
  --data examples/auto_sample_ticks.csv
```

这条命令使用与实盘相同的合约生成、候选排名、激活和退役逻辑，适合先验证“自动选择策略”本身。

扫描当前跨期候选：

```bash
afuture scan \
  --config config/afuture.example.toml \
  --data examples/research_ticks.csv
```

执行短窗口示例 Walk-forward；真实研究应使用更长窗口：

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

## 自动发现与自动交易

当前实盘示例默认使用 `auto` 模式，不要求手工填写具体合约月份。自动层保持很小，只做“选哪个同品种跨期组合”，不会复制策略、风控或执行器。

流程：

```text
CTP 合约目录
  ↓
品种/交易所白名单
  ↓
过滤临近到期，只取每个品种前 3 个可交易月份
  ↓
生成相邻月份：1-2、2-3
  ↓
订阅实时 Tick
  ↓
成交量 + Open Interest + 一档深度
  ↓
异步两腿行情时间配对
  ↓
Z-score + 半衰期 + 平稳性代理 + Net Edge
  ↓
每个品种最多 1 个，账户最多激活 2 个
  ↓
CalendarSpreadStrategy → RiskManager → PairExecutor → CTP
```

自动轮换遵守三个原则：

1. **不为排名轮换强平**：已有持仓即使排名下降，也继续由原策略管理退出。
2. **排名失效后不再重开**：组合退出后，如果不再通过自动候选硬门，会自动退役。
3. **交易日自动更新**：进入新交易日后重新应用到期过滤，新的前排合约自动订阅；临近到期合约停止新增风险。

Scanner 不要求两个 CTP Tick 的时间戳完全相同，只在允许的小时间差内配对，避免异步行情导致自动发现长期得不到样本。

`config/afuture.live.example.toml` 默认只扫描 `m/rb/TA/c/p` 且只启用日盘，是为了让个人系统保持简单。你可以修改白名单；`products = ["*"]` 可以扫描所有支持交易所的期货品种，但不建议个人账户一开始这样做，因为订阅、元数据查询和候选数量都会明显增加。

自动筛选的目标是提高**风险调整后的机会质量**，不能保证高收益或低回撤。最终收益仍取决于真实价差规律、手续费、滑点、成交质量和市场结构变化。

## 为什么不直接用 Z-score 开仓

传统简化模型常用：

```text
spread = near.mid - far.mid
```

但真实双腿成交面对的是：

```text
多价差开仓 = near.ask - far.bid
空价差开仓 = near.bid - far.ask
```

`afuture` 会进一步估算：

```text
Net Edge
= 预期均值回归收益
- 往返手续费
- 双腿滑点
- 裸腿风险缓冲
```

只有统计信号和 Net Edge 同时通过，才进入风险预算与盘口检查。这样能避免“回测看起来有价差，实盘一成交优势就消失”的常见套利假象。

## 实盘配置

复制示例：

```bash
cp config/afuture.live.example.toml config/live.toml
```

CTP 密钥只从环境变量读取：

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

生产柜台额外要求双重确认：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

```bash
afuture live --config config/live.toml --confirm-live
```

### 实盘启动安全门

程序不会在登录成功后立即发单。顺序是：

1. CTP 行情和交易登录、合约初始化完成。
2. `auto` 模式读取 CTP 合约目录，恢复上次仍需管理的动态组合，并订阅候选合约。
3. 等待登录之后**新产生**的账户事件和一次完整持仓快照。
4. 静态组合比较本地/CTP 元数据；动态组合的乘数、price tick、保证金和手续费直接从 CTP 获取。
5. 若存在遗留活动订单，先撤单并停机。
6. 本地期望持仓与柜台完整持仓逐合约对账。
7. 已存在 Kill Switch 时，只有本次会话元数据校验和持仓对账都通过才允许解除。
8. 进入 `RUNNING`；自动层持续观察候选，但只有通过全部硬门的少数组合才会被激活。

如果人工交易或其他程序改变了同一账户的仓位，系统会把未知成交/持仓漂移视为异常。因此建议为该系统使用独立期货账户，至少不要让多个程序同时交易同一批合约。

## 异常状态机

```text
RUNNING
   │
   ├─ 普通风险/数据异常 ───────────→ HALTED
   │
   └─ 双腿失衡/紧急退出失败 ─────→ REDUCE_ONLY
                                      │
                                      ├─ 持续撤单、FAK 只减仓
                                      │
                                      └─ 风险恢复 → HALTED → 人工复核
```

`HALTED` 只表示程序不再增加风险，并不等于仓位一定已经安全。对于裸腿场景，系统会先进入 `REDUCE_ONLY` 尝试降低风险，然后才进入需要人工复核的 `HALTED`。

## 默认风控

示例默认值面向约 50 万元级别的谨慎研究起点，不构成资金或品种建议：

| 规则 | 示例值 |
|---|---:|
| 最大保证金 / 权益 | 35% |
| 最小可用资金 / 权益 | 50% |
| 单交易日最大亏损 | 1% |
| 权益高水位最大回撤 | 8% |
| 最大同时套利组合 | 3 |
| 单合约最大手数 | 10 |
| 风险预算 / 权益 / 组合 | 0.20% |
| 最小一档深度倍数 | 2x |
| 最大 bid/ask 宽度 | 4 ticks |
| 涨跌停最小距离 | 3 ticks |
| 临近到期禁开仓 | 5 天 |
| 普通报单频率 | 20 次/分钟 |

实盘必须按具体品种波动、账户手续费和期货公司保证金重新设置。

## 研究晋级原则

`accept` 不是“找最高收益参数”，而是：

1. Train + Validation 选择参数。
2. 参数必须位于相邻参数也表现稳定的区域；多个孤立峰值不自动选冠军。
3. OOS 只用于验收，不参与前面的参数选择。
4. 对最终候选执行 1x/1.5x/2x 等交易成本压力。
5. OOS 正收益比例、最大回撤和成本压力同时达标才晋级。

真实实盘前还应覆盖多年份、不同月份合约、不同波动状态、手续费/滑点扩大和删除单一优势区间等压力场景。

## 项目结构

```text
afuture/
  broker/
    base.py             # 柜台统一接口
    sim.py              # 模拟/保守撮合
    ctp.py              # VeighNa CTP 实盘适配
  models.py             # 统一领域模型
  auto.py               # CTP 合约发现、候选排名和动态组合生命周期
  economics.py          # 可成交价差与 Net Edge
  strategy.py           # 跨期均值回归与结构失效
  risk.py               # 账户、市场和动态仓位风控
  portfolio_risk.py     # 滚动相关性与风险组
  execution.py          # 双腿执行、回滚和只减仓修复
  engine.py             # 实时/回放统一事件链
  health/monitor.py     # 健康门
  metadata.py           # CTP 实时参数校验
  state.py              # 可校验状态持久化
  scanner.py            # 候选扫描
  calibration.py        # 稳定参数区域选择
  research.py           # Walk-forward/OOS/Stress
  alerts.py             # 本地/Webhook 告警
  journal.py            # 结构化审计日志
  cli.py                # 命令行入口
```

进一步说明：

- [架构与数据流](docs/architecture.md)
- [实盘与恢复](docs/live-trading.md)
- [数据、回放与研究](docs/data-and-backtest.md)
- [生产上线检查表](docs/production-checklist.md)

## 仍然不能由代码仓库替代的验证

代码可以建立完整的软件闭环，但以下事项只有你的期货公司测试/真实环境才能最终确认：

- CTP 前置地址、BrokerID、AppID/AuthCode 和账户权限。
- 账户实际保证金、手续费和平今费查询结果。
- 测试柜台下单、撤单、部分成交、断线重连、夜盘交易日。
- 特定期货公司柜台对 CTP 查询频率、风控和报单限制的差异。

推荐上线顺序：**历史研究 → 保守回放 → CTP 测试柜台 → 极小真实仓位 → 多交易日验证 → 再扩大风险预算**。
