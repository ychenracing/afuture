# afuture

`afuture` 是一个面向个人使用的国内商品期货自动交易程序。当前保留两条**账户互斥**的正式交易链：

1. **Calendar Spread**：同品种相邻月份跨期套利，支持静态 pair 与 Auto 自动发现；
2. **Execution-Aligned Directional Portfolio**：为追求更高收益新增的 50 品种方向组合，使用冻结的多 Alpha 模板、低 gross leverage、CTP 主力合约自动选择和统一风险/执行门。

项目仍保持“小而完整”：不引入数据库、微服务或第二套账户状态机。Broker 是账户/订单/成交/持仓真相，`RiskManager`、Kill Switch、`REDUCE_ONLY`、Shadow 和执行质量证据继续作为统一生产边界。

## 当前结论

### 100% 年化历史目标：已在允许有限过拟合的口径下达到

最终冻结的 directional 方案使用：

- 50 个中国商品期货品种；
- specific-contract 日线；
- point-in-time 主力合约与 20 天交割黑窗；
- 信号只使用前一日收盘及此前历史；
- 旧权重承担前收→次开 gap，新目标权重承担次开→收盘收益；
- 5bp 单边基础成本、15bp 单边压力成本；
- **gross notional 硬上限 2.0x**；
- Final OOS 明确标记 `pristine_final_oos=false`。

`2024-08-21 ~ 2026-08-20` 最终 specific-contract / next-open 结果：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.46%** | **58.14%** |
| 累计收益 | **317.54%** | **142.94%** |
| 最大回撤 | **27.41%** | **32.96%** |
| Sharpe | **1.69** | **1.22** |
| 活跃交易日 | 481 / 484 | 481 / 484 |
| 最大 gross notional | **2.0x** | **2.0x** |

30bp 单边极端成本下最近两年仍约 **9.96% 年化**，但最大回撤约 **46.76%**。

### 必须同时看的反证

上述 107.46% **不是独立泛化证明，也不是未来收益承诺**。模板池在已经观察过的历史上做过收益优先选择；用户允许有限程度的过拟合，因此仓库保留这个历史目标结果，但不把它包装成 pristine OOS。

`2026-02-21 ~ 2026-08-20` Final OOS 已被多轮研究观察：

- base 年化约 **-10.73%**；
- base 最大回撤约 **24.98%**；
- stress 年化约 **-31.42%**。

因此实际使用时应把“107.46% 最近两年历史目标已达到”和“未来泛化尚未证明”同时成立地理解。

完整证据见 [`docs/return-target-100-evidence.md`](docs/return-target-100-evidence.md)。

## 数据真实性与限制

最终 L4 使用 GitHub Actions 抓取并固定的真实公开数据：

- 50 个品种；
- 3,000 个候选具体合约请求；
- 2,540 个可用具体合约；
- 约 495,086 行具体合约日线；
- 最终产品 `missing_next_contract_returns=0`；
- t→t+1 收益始终来自 t 日已选择的**同一具体合约**；
- 不把连续合约换月跳空计入可交易 Alpha。

仍存在三个重要限制：

1. 历史公开数据没有过去数年的完整 L1 bid/ask/depth、部分成交和拒单；
2. L4 使用目标 notional 权重，不是对某一个真实账户逐日按合约乘数、整数手数、保证金变化完全重建的资金曲线；
3. 生产会受整数手数、盘口深度、保证金、涨跌停、交易时段和 CTP 实际成交约束，实际收益可能显著低于历史研究值。

## 两条正式交易链

### 1. Calendar Spread / Auto

```text
CTP Catalog / Tick
      ↓
AutoPairManager
expiry/front-3/adjacent months
      ↓
volume/OI/sync/stationarity/half-life/Net Edge
      ↓
CalendarSpreadStrategy
      ↓
PortfolioRisk + RiskManager
      ↓
PairExecutor → Broker
```

能力包括：

- 自动读取 CTP 合约目录；
- 到期过滤、front-3、相邻月份组合；
- volume / Open Interest / L1 depth；
- executable spread、Z-score、stationarity、half-life；
- 手续费、滑点、legging buffer 后的 Net Edge；
- 动态风险预算；
- FAK 双腿、撤单、只减仓回滚；
- managed 与 open-eligible 分离；
- bounded warm history 与非阻塞元数据预取。

旧 corrected M/OI 同品种策略仍然没有通过经济晋级门：最近两年约 `+1.028R`、16 个邻域 `0/16`、2% 风险代理年化约 `1.07%`。它不与新的 directional 结果混淆。

### 2. Execution-Aligned Directional Portfolio

```text
Sina/AKShare 连续 OHLC（只做信号）
      ↓
冻结 96-template pool
breakout / tsmom / momentum / MA / reversal / acceleration
      ↓
10 日历史 meta score → 每 5 日轮动 → 同时 3 个模板
      ↓
目标产品权重，gross <= 2.0x
      ↓
CTP Catalog + 实时 Tick
point-in-time OI/volume 选择当前具体合约
      ↓
整数手数目标 → 先减风险 → 再开新风险
      ↓
RiskManager → FAK → CTP
```

生产 directional 模式具有以下硬边界：

- 与 static calendar pairs / Auto **账户互斥**；
- Universe 固定为研究验证的 50 品种；
- 模板池、meta lookback=10、meta rebalance=5、meta count=3 固定；
- 产品信号只依赖前一收盘及此前数据；
- gross target ≤2.0x；
- 当前具体合约从 CTP 目录和实时 OI/volume 自动选择；
- 旧合约/超额风险必须先减仓，确认后下一周期才允许新增风险；
- 新风险逐合约检查 fresh quote、bid/ask、depth、涨跌停距离；
- 开仓批次再次检查账户、保证金、可用资金和单合约手数；
- Broker 仍是持仓与成交唯一真相；策略不会自行“假定成交”。

## 风控与异常处理

统一生产硬门：

- 最大保证金率；
- 最小可用资金率；
- 日亏损门；
- 权益高水位总回撤门；
- 单合约手数上限；
- 报单频率；
- fresh quote；
- bid/ask 宽度；
- top-of-book depth；
- 涨跌停距离；
- `RUNNING → REDUCE_ONLY → HALTED`；
- Kill Switch；
- 启动时活动订单、持仓和本地状态对账。

方向组合的 2.0x 是**目标 gross notional 上限**，不是绕过保证金门的授权；真实开仓仍会被账户风险门缩小或拒绝。

## 安装

研究/测试：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

CTP + directional 信号源：

```bash
python -m pip install -e ".[live,dev]"
```

## 常用命令

### Calendar Spread / Auto

```bash
afuture validate --config config/afuture.auto-replay.example.toml
afuture replay --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv
afuture data-check --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv
afuture accept-auto --config config/afuture.auto-replay.example.toml --data path/to/ticks.csv
```

### Directional Shadow / Doctor / Live

先复制并填写：

```text
config/afuture.directional-live.example.toml
```

然后：

```bash
# 配置校验
afuture validate --config config/afuture.directional-live.example.toml

# 真实 CTP 行情/目录/元数据 + 本地虚拟账户
afuture shadow --config config/afuture.directional-live.example.toml --duration-seconds 3600

# 只检查 CTP，不报单
afuture doctor --config config/afuture.directional-live.example.toml

# 真实生产；仍需要显式风险确认
afuture live --config config/afuture.directional-live.example.toml --confirm-live
```

真实 CTP 环境还必须设置：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

CTP 用户名、密码、BrokerID、AppID/AuthCode 仍只从环境变量读取，不写入仓库。

## Shadow 与真实资金门

**代码级生产接线完成 ≠ 可以直接放大真实资金。** Directional Alpha 已经接入正式 runtime，但真实资金前仍要求：

1. 多交易日 CTP Shadow；
2. 真实 bid/ask/depth 与 historical cost 假设对比；
3. 测试柜台 FAK、部分成交、拒单、断线、平今/平昨验证；
4. 极小真实仓位；
5. 持续 execution-quality / 结算单核对；
6. 新发生、此前未见的未来数据继续验证。

详见 [`docs/production-checklist.md`](docs/production-checklist.md)。

## 验证层级

```text
L1  局部单测 / 因果回归
L2  相关策略 / runtime smoke
L3  broad real-data family search
L4  specific-contract / roll-safe / execution-aware 经济门
Final  主 CI + code review
```

昂贵历史抓取不是日常内循环。已经完成的 L4 数据只在策略、执行时点、成本或合约选择发生实质变化时重跑。

## 关键文档

- [100% 年化目标最终历史证据](docs/return-target-100-evidence.md)
- [架构与数据流](docs/architecture.md)
- [数据、回放与研究](docs/data-and-backtest.md)
- [实盘、Shadow 与恢复](docs/live-trading.md)
- [生产上线检查表](docs/production-checklist.md)
- [此前套利研究收口记录](docs/research-final-evidence.md)
- [两年 corrected M/OI 数据证据](docs/two_year_real_data_validation.md)

## 最终状态

`afuture` 当前已经具备：

- 自动发现/筛选同品种跨期套利；
- 自动运行冻结的高收益 directional 多品种组合；
- 自动选择当前具体合约；
- 自动计算目标手数并下单；
- 完整账户/保证金/盘口/状态风险门；
- specific-contract next-open 历史口径下 **107.46% 年化**的已观察目标结果。

同时必须保留事实边界：该高收益结果具有明确历史选择偏差，Final OOS 并未独立通过，真实 L1 与小资金执行尚未证明能够兑现同等收益。