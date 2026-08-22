# afuture

`afuture` 是面向个人使用的国内商品期货自动交易程序，保留两条**账户互斥**的正式交易链：

1. **Calendar Spread / Auto**：同品种相邻月份跨期套利；
2. **Execution-Aligned Directional Portfolio**：冻结 50 品种、96-template 的方向组合。

项目只保留一套账户/订单/成交/持仓真相：Broker/CTP。Directional 不建立第二状态机，仍复用 `RiskManager`、Kill Switch、`REDUCE_ONLY`、状态持久化、启动对账、Shadow 和执行质量证据。

## 当前结论：必须区分两层历史证据

### 1. Float-notional specific-contract L4

最终冻结 directional 研究口径：

- 区间：`2024-08-21 ~ 2026-08-20`；
- 50 个中国商品期货品种；
- specific-contract 日线；
- D 日最终 OI/volume 决定 D+1 具体合约，20 天交割黑窗；
- 截至 D 收盘的信息决定 D+1 产品权重；
- 旧仓承担 `D close → D+1 open`，新目标承担 `D+1 open → close`；
- gross target notional 硬上限 **2.0x**；
- 5bp Base、15bp Stress；
- 模板池存在明确历史选择偏差，`pristine_final_oos=false`。

最终官方 artifact：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |
| gross target 上限 | **2.0x** | **2.0x** |

30bp Extreme：最近两年年化约 **5.09%**、最大回撤约 **43.51%**。

已经观察过的 `2026-02-21 ~ 2026-08-20` Final OOS：Base 年化约 **-10.73%**、Base 最大回撤约 **27.41%**、Stress 年化约 **-31.42%**。因此 107.4623% 只是 selection-biased 的已观察历史结果，不是独立泛化证明或未来收益保证。

### 2. Production-mechanics proxy

当前代码额外把**同一冻结权重**放入账户机械和现有风险门，不重新搜索 Alpha/参数。最近两年结果：

| 指标 | Base 5bp / 12% margin proxy | Stress 15bp / 15% margin proxy |
|---|---:|---:|
| 年化收益 | **6.7861%** | **3.4290%** |
| 累计收益 | **13.4401%** | **6.6897%** |
| 最大回撤 | **5.5680%** | **5.3020%** |
| 活跃交易日 | **20 / 484** | **17 / 484** |
| 最终权益（初始 500,000） | **567,200.36** | **533,448.53** |
| margin reject days | 0 | **14** |
| 最终致命风险门 | `daily loss limit reached` | `margin ratio limit reached` |
| 停机日期 | **2024-09-19** | **2024-09-19** |

这意味着：**当前 production-account 语义并没有复现 100% 年化历史目标。** Base 在 2024-09-19 触发 5% 日亏损门后 flatten / halt；Stress 还存在显著 margin opening reject。

Proxy 较小的 5% 左右最大回撤不能解释成“真实账户更稳”，主要原因是账户很早就被风险门停掉，后续绝大部分历史不再承担风险。

本轮没有为了恢复漂亮数字而放宽：

- `max_daily_loss_ratio=5%`；
- `max_total_drawdown_ratio=30%`；
- `max_margin_ratio=35%`；
- `min_available_ratio=25%`；
- gross target ≤2.0x。

详细证据：

- [`docs/return-target-100-evidence.md`](docs/return-target-100-evidence.md)
- [`docs/directional-production-mechanics-evidence.md`](docs/directional-production-mechanics-evidence.md)

## Directional 生产数据流

```text
Sina/AKShare 连续 OHLC（只做 signal/meta）
        ↓
ExecutionAlignedAggressivePolicy
冻结 96-template pool
        ↓
截至完整交易日 D 的产品权重

CTP Tick.trading_day
        ↓
DirectionalActivityTracker
冻结 D 日具体合约最终 OI/volume
        ↓
D+1 concrete contract selection
        ↓
D+1 fresh quote / depth / limit / metadata
        ↓
integer target lots
        ↓
reductions → Broker 确认 → 后续 cycle openings
        ↓
RiskManager → FAK → Broker
```

关键边界：

- Universe、96-template pool、meta lookback=10、rebalance=5、active templates=3 全部冻结；
- `DirectionalActivityStore` 只持久化 market-selection 证据，不拥有账户状态；
- 当前交易日尚未完成的累计 OI/volume 不能改变上一完整交易日冻结的选约；
- signal freshness 先要求 OHLC 覆盖 completed activity day，小时上限只做第二道长期停更门；
- provider 临时失败但缓存已覆盖 required day 时可继续；
- completed activity 比已完成 signal day 落后时 fail-closed，不允许用陈旧 snapshot 开仓；
- required signal/activity 缺失且已有 directional risk → `REDUCE_ONLY`；账户为空时只拒绝新增风险；
- 新目标合约不可用不得阻塞其它确定性 reduction；已有同产品风险冻结当前手数；
- Broker 仍是成交和持仓唯一真相，策略不自行假定成交。

## Directional execution quality

同一个 `ExecutionQualityRecorder` 同时保留 pair 和 directional 证据。Directional 事件：

- `directional_rebalance`：signal/activity day、target lots、reductions/openings、planned turnover；
- `directional_fill`：order/symbol、expected/fill price、multiplier、slippage bps、commission；
- `directional_cycle`：realized turnover、tracking error、完成延迟、partial/rejected count。

真实 fill 只来自 Broker trade callback；quality 层不会修改账户或持仓。`afuture quality-report` 保留 pair 兼容字段并增加 `directional` 子汇总。

## Calendar Spread / Auto

原套利路径保持不变：

```text
CTP Catalog / Tick
→ AutoPairManager
→ CalendarSpreadStrategy
→ PortfolioRisk + RiskManager
→ PairExecutor
→ Broker
```

支持 point-in-time catalog、front-3/adjacent months、activity/sync/stationarity/half-life/Net Edge、动态风险预算、FAK 双腿、partial rollback、managed/open-eligible 分离、bounded warm history 和非阻塞 metadata。

旧 corrected M/OI 策略最近两年约 `+1.028R`，16 个邻域 `0/16`，2% 风险代理年化约 `1.07%`，仍未通过高收益经济门。

## 风险与恢复

统一生产硬门包括：

- 最大保证金率、最小可用资金率；
- 日亏损、权益高水位总回撤；
- 单合约手数和报单频率；
- fresh quote、bid/ask、top-of-book depth；
- 涨跌停距离和交易时段；
- `RUNNING → REDUCE_ONLY → HALTED`；
- Kill Switch；
- 启动时活动订单、完整持仓和本地状态对账。

Directional 重启不恢复第二份“策略仓位”：`RuntimeState.positions` 与 Broker 完整持仓逐合约一致才允许继续，不一致直接 fail-closed。

## 安装

研究/测试：

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

CTP + AKShare：

```bash
python -m pip install -e ".[live,dev]"
```

## 常用命令

```bash
# Calendar / Auto
afuture validate --config config/afuture.auto-replay.example.toml
afuture replay --config config/afuture.auto-replay.example.toml --data examples/auto_sample_ticks.csv
afuture accept-auto --config config/afuture.auto-replay.example.toml --data examples/research_ticks.csv

# Directional
afuture validate --config config/afuture.directional-live.example.toml
afuture shadow --config config/afuture.directional-live.example.toml --duration-seconds 3600
afuture doctor --config config/afuture.directional-live.example.toml
afuture live --config config/afuture.directional-live.example.toml --confirm-live

# Execution quality
afuture quality-report --config config/afuture.directional-live.example.toml --output runtime/execution_quality_report.json
```

真实 CTP 凭证只从环境变量读取；真实生产还要求：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

## 真实资金门

当前 production-mechanics 证据已经明确表明：**不能把 107.4623% 直接作为当前生产配置可兑现收益。** 真实资金前仍必须完成：

1. 多交易日 CTP Shadow；
2. previous-day activity snapshot 与实际主力切换抽查；
3. modeled vs realized turnover/slippage/commission；
4. 实际 margin/risk-off 与 proxy 差异；
5. 测试柜台 FAK、partial、reject、断线、平今/平昨和换月；
6. 极小真实仓位；
7. 新发生、此前未参与选择的未来数据。

在这些证据出现前，不应为了追求历史 100% 数字放宽账户风险硬门。

详见 [`docs/production-checklist.md`](docs/production-checklist.md)。

## 验证层级

```text
L1  局部因果/风控/手数/quality 单测
L2  directional runtime + restart smoke
L3  broad research / production-mechanics proxy
L4  specific-contract / roll-safe / execution-aware 经济证据
Final  Python 3.10/3.13 主 CI + review
```

昂贵 L4 只在策略公式、合约选择、执行时点、成本或数据方法发生实质变化时运行。文档和无行为清理不重复 L4。
