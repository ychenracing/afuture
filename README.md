# afuture

`afuture` 是一个面向个人使用的国内商品期货自动套利程序。它刻意保持“小而完整”：自动读取 CTP 合约目录、发现和筛选候选、进行风险预算、提交双腿订单、处理异常与恢复，并保留 Shadow/执行质量证据，而不是建设一个庞大的交易平台。

```text
CTP 合约目录 / 历史 point-in-time Universe
        ↓
AutoPairManager：到期过滤 → front-3 → 相邻月份
        ↓
活动度 / 统计质量 / executable spread / Net Edge
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

当前**生产执行链仍只处理同品种相邻月份跨期套利**。跨品种经济关系（例如 BU/FU）仅属于研究证据，没有被悄悄接入生产下单。

## 当前状态

工程闭环已经完成，但“年化收益 100% 以上且低回撤”**没有被真实历史证据证明**。

这不是把目标降低了，而是把事实和目标分开：系统具备自动发现、筛选和下单能力；经济 Alpha 仍必须通过真实 OOS、成本压力、Shadow 和测试柜台证据后才允许生产放大。

### corrected M/OI 同品种跨期证据

对原日频 log-ratio 规则修正了中国期货交易日、两腿同步时间戳、front-3、20 天交割黑窗、历史 listing 和当时可见成交量后：

| 项目 | corrected 结果 |
|---|---:|
| prior 资格 → 后续 Train | 4 笔，约 **-1.958R** |
| Final OOS | 2 笔，约 **+0.296R** |
| 最近两年 | 5 笔，约 **+1.028R** |
| 16 个局部邻域 | **0 / 16** 通过 |
| 2% 单笔风险资本代理年化 | 约 **1.07%** |
| 100% 年化目标 | **未达到** |

因此旧的“M/OI 已验证生产 Alpha”结论作废。`config/afuture.live.example.toml` 只作为 test/Shadow 研究模板，风险预算保持测试级。

### 扩展策略研究

收益不足后，研究参考了：

- `rolling-panda-san/notebooks`：商品 term structure、basis/momentum/reversal；
- `aranjan-4702/pairs-trading-egarch`：经济关系过滤、滚动残差、持久性与 volatility regime；
- `kieranjwood/slow-momentum-fast-reversion`：慢趋势与快速反转思想。

没有直接复制第三方代码或引入重型 ML/GARCH 依赖，而是用轻量、可消融、可因果验证的实现测试同类思想。

研究结论：

- 约 50 个中国商品期货主连上的 cross-sectional momentum / slow-fast / reversal / skewness：**没有稳定家族**；
- broad economic-pair L3：发现一簇可重复的经济关系残差均值回归候选；
- specific-contract roll-safe L4：BU/FU（prior 还包含 PP/V）在具体合约、真实换月语义下仍为正，说明 broad 结果并非单纯换月拼接假象；
- 但在 **30bp 单边压力成本、最多 1 个 pair、gross leverage ≤2x** 下，最近两年年化约 **4.20%**、最大回撤约 **12.88%**；Final OOS 年化约 **5.78%**、最大回撤约 **11.78%**；
- 60 分钟 BU/FU + PP/V intraday：**0 / 24** 个预注册 profile 通过 pre-OOS 门；
- soybean crush、steel/coke margin、polymer/base-metals 等多腿结构研究没有形成足够稳定且高收益的家族，不值得增加三腿执行复杂度。

所以 `alpha_survives_specific_contract=true` 只表示 BU/FU 的**研究信号**经受住具体合约换月复验，不表示它已经成为生产策略；`target_met=false` 仍然成立。

完整研究收口见 [最终研究证据结论](docs/research-final-evidence.md)。

> 历史公开数据没有过去数年的完整 L1 bid/ask/depth。日线/60 分钟研究只能证明信号层经济性，不能冒充真实盘口成交证据。

## 已修正的回测与执行真实性问题

本轮 review 修正或确认了以下关键边界：

- 夜盘按中国期货**交易日**而不是自然日归属；
- 同步策略只接受两腿相同的采样时间戳，不用错位 bar 补腿；
- 历史 Universe 按当日可见 listing/expiry 构造，避免未来合约提前出现；
- front-3 与交割黑窗按 point-in-time 语义计算；
- 历史 volume 使用截至决策时点真正可见的累计值；
- 回放订单限速、裸腿超时、健康检查使用事件时间，不把多年回放压缩成 CPU 秒；
- 组合相关性按时间桶交集对齐；
- SimBroker 的旧成交事件先于同一 Tick 的新策略决策；
- 风控/执行拒单后恢复策略真实状态；
- specific-contract 研究的 t→t+1 收益来自 t 日选择的**同一具体合约**，不把换月跳空算成 Alpha；
- Final OOS 已被多轮研究观察，当前代码明确标记为 **non-pristine**。

## 核心能力

### 自动发现和筛选

- CTP 合约目录、品种、交易所和到期日；
- 到期过滤、front-3、同品种相邻月份组合；
- volume / Open Interest / 一档深度；
- 双腿行情同步；
- Z-score、半衰期、stationarity；
- executable bid/ask spread；
- 手续费、滑点、legging buffer 后的 Net Edge；
- 少量候选激活；失去开仓资格的已有持仓仍保留管理/退出权。

### 风控和执行

- 动态风险预算手数；
- 保证金、可用资金、日亏损、权益高水位回撤；
- bid/ask 宽度、深度、涨跌停距离、交易时段；
- risk-group 与时间对齐的组合相关性；
- FAK 双腿、薄腿优先、撤单和只减仓回滚；
- 平今/平昨；
- `RUNNING → REDUCE_ONLY → HALTED`；
- 裸腿异常只减仓；
- 未知订单、成交或持仓漂移 fail-closed。

### 状态与生产证据

- schema/sequence/SHA-256 状态文件；
- 动态 pair 重启恢复和有限 warm history；
- CTP 实时乘数、tick、保证金、手续费元数据门；
- Health Monitor；
- JSONL 审计和文件/Webhook 告警；
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

# Auto Portfolio Walk-forward/OOS/Stress
afuture accept-auto --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv

# 真实 CTP 行情 + 本地模拟订单
afuture shadow --config config/afuture.live.example.toml

# 无报单检查 CTP 登录/快照/目录/元数据
afuture doctor --config config/afuture.live.example.toml

# 执行质量汇总
afuture quality-report --config config/afuture.live.example.toml --shadow
```

真实生产仍有双重显式门：生产环境配置 + `AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK` + `--confirm-live`。

## 研究验证层级

遵循影响范围驱动验证：

```text
L1  局部单测 / 因果回归
L2  相关策略或模块 smoke
L3  broad universe / family screen
L4  specific-contract / 完整经济证据门
```

昂贵研究 workflow 使用手动 milestone 门，不作为每个小改动的内循环：

- `.github/workflows/research-2y.yml`：corrected 同品种跨期两年证据；
- `.github/workflows/research-broad.yml`：经济关系 broad L3；
- `.github/workflows/research-specific-pairs.yml`：具体合约 roll-safe L4。

普通工程正确性由主 CI 负责。

## 为什么现在停止继续历史调参

当前 Final OOS 已经被多轮研究观察，不再是 pristine holdout。继续在同一历史上扩大参数、特征、关系或杠杆搜索，会提高过拟合概率，而不是增加新证据。

下一步真正能改变结论的是：

1. 新发生、此前未见的未来交易日；
2. 真实 CTP L1 Shadow 的 bid/ask/depth、手续费和滑点；
3. 测试柜台的部分成交、拒单、断线、平今平昨和恢复；
4. 极小真实仓位的持续 realized Net Edge 与实际回撤。

在这些证据出现之前：

- 不通过高杠杆把 4%–6% 历史收益放大成“100%”；
- 不在已看过的 OOS 上继续大规模调参；
- 不做每品种独立参数挖掘；
- 不把研究中的跨品种 pair 偷接到生产执行；
- 不增加 GUI、数据库、微服务等与当前瓶颈无关的复杂度。

## 文档

- [架构与数据流](docs/architecture.md)
- [数据、回放与研究](docs/data-and-backtest.md)
- [最终研究证据结论](docs/research-final-evidence.md)
- [研究清理清单](docs/research-cleanup-inventory.md)
- [两年 corrected 数据证据](docs/two_year_real_data_validation.md)
- [实盘、Shadow 与恢复](docs/live-trading.md)
- [生产上线检查表](docs/production-checklist.md)

## 最终结论

`afuture` 当前是一个**工程上完整、能够自动发现/筛选/下单，但尚没有被独立真实证据证明可以年化 100% 的自动套利框架**。

代码不会把未达到的收益目标写成已经实现。只有未来新 OOS + 真实 L1 Shadow + 测试柜台/小资金证据同时证明持续 Net Edge 后，才允许扩大生产风险。
