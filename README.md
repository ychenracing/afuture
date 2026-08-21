# afuture

`afuture` 是一个面向个人使用的国内商品期货**同品种跨期套利自动交易程序**。目标不是建设交易平台，而是保持一条小而完整、可以自己发现机会、自己筛选、自己下单、出现异常能够只减仓并恢复的闭环。

```text
CTP 合约目录 / 历史 point-in-time Universe
        ↓
AutoPairManager：到期过滤 → front-3 → 相邻月份
        ↓
活动度 / 均值回归 / 可成交价差 / Net Edge
        ↓
少量 open-eligible pairs
        ↓
CalendarSpreadStrategy
        ↓
PortfolioRisk + RiskManager
        ↓
PairExecutor → CTP
        ↓
状态、审计、告警、Execution Quality
```

系统基于 VeighNa `vnpy_ctp` 接入 CTP。策略范围刻意保持小：只做同品种跨期，不增加跨品种、期现、期权、高频做市、在线自动调参、GUI、数据库或微服务。

## 当前结论：工程闭环完成，Alpha **未晋级**

2026-08-21 对历史证据做了完整 Code Review，并修正了多个会高估回测可交易性的口径：

- 夜盘按**中国期货交易日**而不是自然日归属；
- 两腿必须在 `22:55-23:00` 内有**完全相同的 60 分钟时间戳**，不能用日盘或错位 bar 补腿；
- 历史 Universe 每个交易日先做 20 天交割黑窗，再只保留 **front 3** 合约并生成相邻月份；
- 历史 volume 使用该期货交易日截至采样时刻的累计值，并与生产 `min_volume=1000` 对齐；
- 历史 listing 可进入 `ContractInfo`，防止未来合约提前进入回放 Universe；
- 历史报单限速、裸腿超时、行情健康都使用**事件时间**，不再把几年的回放压缩成几秒 CPU 时间；
- 组合相关性按时间桶交集对齐；
- SimBroker 延迟成交回报先于同一 Tick 的新策略决策；
- 风险/执行拒单后恢复真实策略状态，不再破坏入场锚点。

在这些修正以后，冻结的日频 log-ratio 相对价值规则得到的 corrected diagnostic 是：

| 项目 | 结果 |
|---|---:|
| 前置两年资格品种 | `MA` |
| 前置资格 → 后续 Train | **4 笔，-1.958R** |
| 当前 Train+Validation 资格品种 | `M` |
| Final OOS | **2 笔，+0.296R** |
| Final OOS 最大回撤 | **-0.042R** |
| 2024-08-21~2026-08-20 | **5 笔，+1.028R** |
| 最近两年最大回撤 | **-0.042R** |
| 16 个局部邻域稳定门 | **0 / 16 通过** |
| 2% 单笔风险代理两年总收益 | **约 +2.07%** |
| 2% 风险代理年化 | **约 +1.07%** |
| 目标年化 | **100%** |
| 目标达到 | **否** |

因此，旧的 `M/OI` “已验证生产 Alpha”结论已经作废。`config/afuture.live.example.toml` 现在只作为 **test/Shadow 研究模板**，风险预算已降到测试级；不能通过提高杠杆或风险预算把 1% 左右的证据“放大”为 100% 年化。

> **事实边界：** 真实历史数据是 AKShare/Sina specific-contract 60 分钟 OHLC、成交量和持仓量。公开源没有过去两年的完整 L1 bid/ask/depth，因此历史执行成本只能使用保守代理；历史结果不能冒充真实盘口成交证据。

## 对三个参考项目的迁移结果

收益不足后，项目按要求研究了：

- `rolling-panda-san/notebooks`：期限结构、basis/carry momentum 与 reversal；
- `pairs-trading-egarch`：关系持久性、波动率 regime；
- `slow-momentum-fast-reversion`：慢趋势 + 快速反转 / change-point 思想。

没有直接复制第三方代码，也没有引入 TensorFlow/EGARCH 等重依赖，而是实现轻量、可消融的同类统计思想。预注册的 11 个 curve-family 配置覆盖：

```text
log-ratio mean reversion
basis reversal
basis momentum
slow-momentum-fast-reversion
```

在 Train + Validation + 2x 成本 + 邻域稳定门下：

```text
family_support = {}
stable_family_found = false
```

所以这些实验能力**没有晋级生产默认**。这是有价值的负结果：继续扩大相同历史数据上的参数自由度只会增加过拟合风险。

## 核心生产能力

### 自动发现和筛选

- 从 CTP 合约目录读取品种、交易所、到期日；
- 到期过滤、front-3、相邻月份组合；
- volume / Open Interest / 一档深度；
- 异步双腿行情同步；
- Z-score、半衰期、平稳性；
- 可成交方向 bid/ask spread；
- 手续费、滑点、legging buffer 后的 Net Edge；
- 只激活少量候选；已有仓位失去资格后仍管理退出，但立即失去新开仓权。

### 策略

正式链路支持 absolute spread 和 log-ratio 相对价值；当前冻结研究参数保留在示例配置中仅用于复现，不代表已晋级盈利策略。持仓退出使用真实可平仓方向的 executable spread，包含：

- entry-anchored stop；
- 最长持仓；
- 结构均值漂移；
- 波动率突变；
- 回归确认；
- stationarity / half-life 门。

### 风控和执行

- 动态风险预算手数；
- 保证金、可用资金、日亏损、总回撤；
- bid/ask 宽度、深度、涨跌停距离、交易时段；
- risk-group 和时间对齐的组合相关性；
- FAK 双腿、薄腿优先、撤单和回滚；
- 平今/平昨；
- `RUNNING → REDUCE_ONLY → HALTED`；
- 裸腿异常只减仓；
- 未知订单/成交/持仓漂移 fail-closed。

### 生产证据

- schema/sequence/SHA-256 状态文件；
- 动态 pair 重启恢复；
- CTP 实时乘数、tick、保证金、手续费元数据门；
- Health Monitor；
- JSONL 审计；
- 文件/Webhook 告警；
- Shadow 模式；
- candidate / decision / round-trip Execution Quality；
- `doctor` 无报单柜台检查。

## 安装

研究/测试：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

CTP：

```bash
python -m pip install -e ".[live,dev]"
```

## 常用命令

```bash
# 配置校验
afuture validate --config config/afuture.auto-replay.example.toml

# 历史回放
afuture replay --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv

# 数据质量
afuture data-check --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv

# 最终 Auto Portfolio Walk-forward/OOS/Stress
afuture accept-auto --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv

# 真实 CTP 行情 + 本地模拟订单
afuture shadow --config config/afuture.live.example.toml

# 无报单检查 CTP 登录/快照/目录/元数据
afuture doctor --config config/afuture.live.example.toml

# 执行质量汇总
afuture quality-report --config config/afuture.live.example.toml --shadow
```

真实生产仍有双重显式门：生产环境配置 + `AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK` + `--confirm-live`。

## 两年真实数据研究

高成本 L4 workflow：`.github/workflows/research-2y.yml`。

它固定：

```text
2022-08-22~2023-08-20 prior1
2023-08-21~2024-08-20 prior2
2024-08-21~2025-08-20 train
2025-08-21~2026-02-20 validation
2026-02-21~2026-08-20 final OOS
```

品种身份是选择规则的**输出**，不硬编码成验收条件。经济门固定检查：prior-forward、final OOS、两年样本、回撤、2x 成本和 16 个邻域稳定性。脚本正常运行但经济门失败时返回 code `2`，workflow 仍保存完整证据并标记 `accepted=false`；这与工程测试失败严格区分。

当前 final OOS 已在多轮研究中被观察，因此对未来新特征**不再是 pristine holdout**。新策略若继续开发，应使用新的未来数据/Shadow 作为真正增量证据，而不是继续在同一 OOS 上优化。

## 下一步：不继续堆功能

当前最合理的路线不是再增加指标或杠杆，而是：

1. 保持当前工程闭环和低风险 test/Shadow 配置；
2. 连续采集真实 CTP L1、真实手续费和执行质量；
3. 用未来新增数据形成新的未见样本；
4. 只有新 OOS + Shadow + test-cabinet 同时证明 Net Edge 后，才晋级某个品种/参数；
5. 先 1 手、1 个 pair、小风险运行，再决定是否扩大；
6. 没有新证据时保持 Feature Freeze。

不建议：

- 为达到 100% 年化提高杠杆；
- 在已看过的 OOS 上继续大规模调参；
- 每个品种单独拟合参数；
- 全市场 `products=["*"]`；
- ML/AI 选标；
- 新增跨品种/期现/期权策略；
- GUI、数据库、微服务。

## 文档

- [架构与数据流](docs/architecture.md)
- [数据、回放与研究](docs/data-and-backtest.md)
- [两年真实数据证据](docs/two_year_real_data_validation.md)
- [实盘、Shadow 与恢复](docs/live-trading.md)
- [生产上线检查表](docs/production-checklist.md)

## 结论

`afuture` 当前是一个**工程上完整、可以自动发现/筛选/下单、但经济 Alpha 尚未通过 corrected 两年证据门的实盘候选框架**。

软件能力可以继续进入 Shadow 和测试柜台；高收益/低回撤目标仍是目标，不是已经实现的事实。只有新的独立证据真正通过，才允许把策略标记为生产晋级。