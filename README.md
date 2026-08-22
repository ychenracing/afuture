# afuture

`afuture` 是面向个人使用的国内商品期货自动交易程序，保留两条**账户互斥**的正式交易链：

1. **Calendar Spread / Auto**：同品种相邻月份跨期套利；
2. **Execution-Aligned Directional Portfolio**：冻结 50 品种、96-template 的方向组合。

项目只保留一套账户/订单/成交/持仓真相：Broker/CTP。Directional 不建立第二状态机，仍复用 `RiskManager`、Kill Switch、`REDUCE_ONLY`、状态持久化、启动对账、Shadow 和执行质量证据。

## 当前结论

### 已观察历史收益目标

最终冻结 directional 研究口径为：

- 区间：`2024-08-21 ~ 2026-08-20`；
- 50 个中国商品期货品种；
- specific-contract 日线；
- t 日最终 OI/volume 决定 t+1 具体合约，20 天交割黑窗；
- 截至 t 收盘的信息决定 t+1 产品权重；
- 旧仓承担 `t close → t+1 open`，新目标承担 `t+1 open → close`；
- gross target notional 硬上限 **2.0x**；
- 5bp 单边 Base、15bp 单边 Stress；
- 模板池存在明确历史选择偏差，`pristine_final_oos=false`。

最终官方 artifact 的最近两年结果：

| 指标 | Base 5bp | Stress 15bp |
|---|---:|---:|
| 年化收益 | **107.4623%** | **58.1372%** |
| 累计收益 | **306.1855%** | **141.1415%** |
| 最大回撤 | **27.4097%** | **32.9554%** |
| Sharpe | **1.6874** | **1.1525** |
| gross 上限 | **2.0x** | **2.0x** |

30bp 单边 Extreme 的最近两年年化约 **5.09%**，最大回撤约 **43.51%**。

这组数字是**选择偏差下的历史结果**，不是独立泛化证明或未来收益承诺。已经观察过的 `2026-02-21 ~ 2026-08-20` Final OOS 约为：Base 年化 `-10.73%`、Base 最大回撤 `24.98%`、Stress 年化 `-31.42%`。

完整证据见 [`docs/return-target-100-evidence.md`](docs/return-target-100-evidence.md)。

## Production-mechanics proxy

旧 L4 是浮点 notional 权重收益曲线，不等于某个真实账户逐日运行结果。本项目现在额外提供 `DirectionalProductionAcceptance`，在**不重新搜索 Alpha/参数**的前提下，用相同冻结权重逐日加入：

- 上一完整交易日 activity 选择下一交易日具体合约；
- 真实产品 multiplier；
- `equity × weight / (open × multiplier)` 整数向下取整；
- `max_contract_volume`；
- 换月/反转先平旧风险，下一阶段才开新风险；
- 账户日亏损和权益高水位回撤门；
- margin / available-cash 门；
- Base 5bp / Stress 15bp 成本。

历史逐日真实期货公司保证金表不可得，因此该层明确使用统一 margin proxy（Base 12%、Stress 15%，再乘现有 `margin_estimate_buffer=1.25`），**不声称它是历史柜台真实保证金**。最终 proxy 结果和旧 float-notional L4 分开记录，不允许用一个数字替代另一个。

## Directional 生产数据流

```text
Sina/AKShare 连续 OHLC（只做信号/meta）
        ↓
ExecutionAlignedAggressivePolicy
冻结 96-template pool
        ↓
截至完整交易日 D 的信号
        ↓
DirectionalActivityStore
冻结 D 日每个具体合约最终 OI/volume
        ↓
D+1 具体合约选择
        ↓
当前 CTP fresh quote / depth / limit / metadata
        ↓
整数 target lots
        ↓
先 reductions → Broker 确认 → 后 openings
        ↓
RiskManager → FAK → Broker
```

关键生产边界：

- Universe、96-template pool、meta lookback=10、rebalance=5、active templates=3 全部冻结；
- `DirectionalActivityStore` 只持久化 market-selection 证据，不拥有账户状态；
- 当前交易日尚未完成的累计 OI/volume **不能改变**已经由上一完整交易日冻结的选约；
- signal freshness 先要求 OHLC 覆盖 `completed_activity_snapshot.trading_day`，`signal_max_age_hours` 只作为第二道长期停更门；
- provider 临时失败但缓存已覆盖 required signal day 时可继续使用缓存；
- required signal 缺失且账户已有 directional risk 时进入 `REDUCE_ONLY`；账户为空时只拒绝新增风险；
- 某个新目标没有可交易合约时，不得阻塞其它确定性减仓；该产品已有风险被冻结在当前手数，不新增/换月；
- Broker 仍是成交和持仓唯一真相，策略不自行假定成交。

## Directional execution quality

同一个 `ExecutionQualityRecorder` 现在同时保留 pair 和 directional 证据。Directional 新增：

- `directional_rebalance`：signal/activity day、target lots、reductions/openings、planned turnover；
- `directional_fill`：order/symbol、expected/fill price、multiplier、slippage bps、commission；
- `directional_cycle`：realized turnover、tracking error、完成延迟、partial/rejected count。

`afuture quality-report` 保留原 pair 顶层字段，并增加 `directional` 子汇总。真实 fill 只来自 Broker trade callback；quality 层不会修改账户或持仓。

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

支持 point-in-time catalog、front-3/adjacent months、activity/sync/stationarity/half-life/Net Edge、动态风险预算、FAK 双腿、部分成交回滚、managed/open-eligible 分离、bounded warm history 和非阻塞 metadata。

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

# Directional 配置/Shadow/Doctor/Live
afuture validate --config config/afuture.directional-live.example.toml
afuture shadow --config config/afuture.directional-live.example.toml --duration-seconds 3600
afuture doctor --config config/afuture.directional-live.example.toml
afuture live --config config/afuture.directional-live.example.toml --confirm-live

# 执行质量
afuture quality-report --config config/afuture.directional-live.example.toml --output runtime/execution_quality_report.json
```

真实 CTP 凭证只从环境变量读取；真实生产还要求：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

## 真实资金门

代码级生产接线完成仍不等于可以直接按研究风险放大资金。Directional 真实资金前必须完成：

1. 多交易日 CTP Shadow；
2. previous-day activity snapshot 与实际主力切换抽查；
3. modeled vs realized turnover/slippage/commission 对比；
4. 测试柜台 FAK、partial、reject、断线、平今/平昨和换月；
5. 极小真实仓位；
6. 新发生、此前未参与选择的未来数据继续验证。

详见 [`docs/production-checklist.md`](docs/production-checklist.md)。

## 验证层级

```text
L1  局部因果/风控/手数/quality 单测
L2  directional runtime + restart smoke
L3  broad research / production-mechanics proxy
L4  specific-contract / roll-safe / execution-aware 最终经济证据
Final  Python 3.10/3.13 主 CI + review
```

昂贵 L4 只在策略公式、合约选择、执行时点、成本或数据方法发生实质变化时运行。文档和无行为清理不重复 L4。
