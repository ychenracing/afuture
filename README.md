# afuture

`afuture` 是一个面向个人使用的国内商品期货**同品种跨期套利自动交易程序**。目标不是建设庞大交易平台，而是保持一条小而完整、可审计、可停机恢复的生产闭环：

```text
真实数据资格门 → CTP 合约发现 → 自动选相邻月份 → 统计/流动性/Net Edge 筛选
             → 风险预算 → 双腿执行 → 异常只减仓 → Shadow/实盘证据复验
```

系统基于 VeighNa `vnpy_ctp` 接入期货公司 CTP。正式策略只做同一品种不同交割月份的跨期套利，不引入方向性期货、跨品种、期现、期权、高频做市、在线自动调参或机器学习选标。

> 历史回测不能保证未来高收益或低回撤。当前代码已经完成工程闭环和真实历史证据门，但生产晋级仍必须依赖 Shadow、测试柜台和极小真实仓位的实际手续费/滑点/稳定性证据。

## 当前生产策略

截至 2026-08-21 的固定生产候选白名单：

- `M`：豆粕；
- `OI`：菜籽油。

运行时不会写死具体交割合约，而是从 CTP 合约目录中自动选择上述品种当前可交易的相邻月份组合。中心信号参数：

| 参数 | 值 |
|---|---:|
| `signal_transform` | `log_ratio` |
| `lookback` | 25 个日样本 |
| `entry_z` | 2.50 |
| `confirmation_retrace_z` | 0.30 |
| `min_confirmed_entry_z` | 1.75 |
| `exit_z` | 0.75 |
| `stop_z` | 4.00 |
| `entry_trend_window` | 6 |
| `max_entry_z_slope` | 0.75 |
| `min_stationarity_score` | 0.01 |
| `max_half_life` | 60 |
| `max_holding_samples` | 20 天 |
| `daily_sample_window` | 22:55-23:00 |

入场不是看到 `±2.5σ` 就立即逆势交易，而是先武装极端偏离，再等待至少 `0.30σ` 的回归确认，同时仍保留至少 `1.75σ` 的有效偏离。随后还必须继续通过趋势斜率、平稳性、半衰期、成交量、Open Interest、盘口深度、双腿同步和 Net Edge 硬门。

## 两年真实数据证据

主验收来自 AKShare 新浪**具体交割合约** 60 分钟历史，不使用连续合约或随机合成数据。

```text
最近窗口：2024-08-21 ~ 2026-08-20，484 个交易日
独立前置窗口：2022-08-22 ~ 2024-08-20，485 个交易日
研究品种：A C EG FG I M MA OI P PP RB RM SA TA Y
最近两年约 9.9 万根具体合约 60 分钟 K 线
```

最近两年严格切分：

```text
Train       2024-08-21 ~ 2025-08-20
Validation  2025-08-21 ~ 2026-02-20
Final OOS   2026-02-21 ~ 2026-08-20
```

品种和参数资格先由 Train + Validation 固定，Final OOS 只负责否决。在 **2x 保守往返成本** 下，中心方案证据：

| 验收 | 合格品种 | 交易数 | 累计 R | 最大回撤 R | 胜率 |
|---|---|---:|---:|---:|---:|
| 2022-2024 资格 → 下一年 Forward | A | 4 | +0.193 | -0.062 | 50% |
| Train + Validation → Final OOS | M、OI | 4 | +1.208 | -0.042 | 75% |
| 最近两年 M、OI 全窗口（描述性） | M、OI | 15 | +7.588 | -0.182 | 86.7% |

16 组单变量参数邻域中 9 组同时通过前置 Forward 与当前 Final OOS，邻域通过率 `56.25%`。这说明中心点不是单一历史尖峰，但样本数量仍然偏少，因此不继续为了抬高回测收益扩大参数自由度。

风险比例历史代理（不是实盘收益预测）：

| 每笔风险比例 | 两年复合收益代理 | 历史最大回撤代理 |
|---:|---:|---:|
| 1.0% | +7.8% | -0.18% |
| 1.5% | +11.9% | -0.27% |
| 2.0% | +16.1% | -0.36% |

生产示例把 `2%` 作为候选风险预算上限，同时保留 `35%` 保证金上限、`1%` 日亏损熔断和 `8%` 权益高水位回撤熔断；真实手数还会被盘口深度、保证金、单合约上限和 CTP 实时参数进一步压缩。

完整证据见 [`docs/two_year_real_data_validation.md`](docs/two_year_real_data_validation.md)。

## 生产链路

```text
CTP Contract Catalog / Historical Catalog
                  ↓
            AutoPairManager
  M/OI 白名单 + 到期过滤 + 相邻月份
                  ↓
 daily log-ratio / confirmation / stationarity
 volume / OI / depth / executable Net Edge
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

关键约束：

- **可成交价差**：开仓、退出和止损都按真实可成交 bid/ask 方向计算，不用 mid-price 冒充成交；
- **管理权与开仓权分离**：已有持仓即使失去 Auto 资格仍继续管理退出，但不能重新开仓；
- **非阻塞元数据**：保证金/手续费查询在后台预取，Tick 关键路径不等待慢速 CTP 查询；
- **有限 warm history**：只持久化 `lookback + buffer` 级别采样行情，日常重启不从零重新预热；
- **组合风险**：限制同风险组暴露和高相关价差组合；
- **异常状态机**：`RUNNING → REDUCE_ONLY → HALTED`，裸腿先减风险，状态不确定时 fail-closed；
- **状态真相**：本地期望持仓与柜台完整快照分离，未知成交/订单/持仓漂移都会阻止继续开仓。

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

当前实盘适配器针对 `vnpy 4.4.x` 与 `vnpy_ctp 6.7.11.4` 接口行为实现。升级 CTP/VeighNa 后必须重新做测试柜台验收。

## 研究与验收

先检查数据：

```bash
afuture data-check \
  --config config/afuture.auto-replay.example.toml \
  --data path/to/multi_contract_ticks.csv
```

单 pair 诊断：

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

最终 Auto Portfolio 研究入口：

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

`accept-auto` 复用最终生产链，覆盖 Train/Validation/OOS、全局小型参数邻域、成本压力、leave-one-product-out、single-product attribution、remove-best-period、深度 haircut、延迟、market impact、数据缺失、双腿异步和 activity 缺失。

两年真实数据重新抓取属于昂贵的 L4 证据门，`.github/workflows/research-2y.yml` 只保留手动触发；普通小改动不重复抓取和回测相同历史状态。

## Shadow：真实行情，不发真实单

```bash
afuture shadow --config config/live.toml
```

Shadow 使用真实 CTP 行情、合约目录、保证金和手续费元数据，但订单只进入本地保守 `SimBroker`，不会调用真实 CTP `send_order()`。

证据输出：

```text
runtime/shadow_execution_quality.jsonl
runtime/shadow_market_samples/
runtime/shadow_audit.jsonl
```

汇总：

```bash
afuture quality-report --config config/live.toml --shadow
```

Execution Quality 会记录 candidate、decision、round-trip 三层证据，包括预期/实际价差、手续费、滑点、双腿成交时间差、部分成交、rollback、REDUCE_ONLY 和 realized Net Edge。

## CTP Doctor

```bash
afuture doctor --config config/live.toml
```

`doctor` 只检查登录、fresh account/position snapshot、合约目录、少量元数据和交易日，**永远不下单**。FAK/FOK、部分成交、拒单、断线、平今/平昨仍必须在期货公司测试柜台验证。

## 实盘

复制配置：

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

生产柜台还要求：

```text
AFUTURE_LIVE_ACK=I_UNDERSTAND_FUTURES_RISK
```

并显式执行：

```bash
afuture live --config config/live.toml --confirm-live
```

启动顺序：CTP 就绪 → Auto 合约目录/历史恢复 → fresh 账户/完整持仓快照 → 元数据门 → 遗留订单门 → 本地/柜台持仓对账 → `RUNNING`。

## 现在不继续增加什么

当前最大不确定性是 **Alpha 在真实执行后的持续性**，不是功能数量。除非后续 OOS/Shadow/实盘证据明确指出缺口，否则不继续增加：

- 全市场 `products=["*"]`；
- 每商品独立参数；
- 在线自动调参；
- 新套利品类或方向性策略；
- 为提高回测收益扩大风险预算；
- GUI、Web 后台、数据库、Redis、Kafka、微服务。

达到以下条件后应保持 Feature Freeze：多年份 OOS/成本压力不崩塌、Shadow 仍有真实 Net Edge、CTP 测试柜台通过异常场景、极小真实仓位连续运行无状态/对账事故、实际手续费和滑点没有吞掉主要 Edge、实际回撤符合预注册门。此后主要工作是观察、维护和定期 OOS 复验，而不是继续堆特征。

详细说明：

- [架构与数据流](docs/architecture.md)
- [数据、回放与研究](docs/data-and-backtest.md)
- [实盘、Shadow 与恢复](docs/live-trading.md)
- [生产上线检查表](docs/production-checklist.md)
