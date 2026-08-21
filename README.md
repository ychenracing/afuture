# afuture

`afuture` 是一套面向个人使用的国内商品期货**同品种跨期自动套利系统**。目标不是搭建庞大的交易平台，而是把真实数据研究、自动选标、风控、双腿执行、CTP 实盘和异常恢复收敛在一条可验证的生产链路中。

核心原则：**只交易被真实历史证据支持的价差机会；任何行情、账户或双腿状态不确定时，优先减少风险。**

> 历史回测不能保证未来高收益或低回撤。当前版本的改造目标是提高风险调整后的历史收益质量，并通过独立 OOS、2x 成本压力和参数邻域稳定性降低过拟合风险。

## 当前生产策略

正式策略仍只做同一品种不同交割月份的跨期套利，不引入方向性期货、跨品种套利、期权或高频做市。

截至本仓库当前两年真实数据验收，生产白名单为：

- `M`：豆粕
- `OI`：菜籽油

运行时不是写死某两个具体合约，而是从 CTP 合约目录中自动选择这两个品种当前可交易的相邻月份组合。

策略使用：

```text
25 日 log(near / far) 相对价值
    ↓
达到 ±2.5σ 极端偏离
    ↓
不立即逆势交易
    ↓
等待至少 0.30σ 的回归确认
    ↓
仍保留至少 1.75σ 的有效偏离
    ↓
趋势斜率 + 平稳性 + 半衰期过滤
    ↓
实时 bid/ask + 深度 + Open Interest + Net Edge
    ↓
风险预算定仓
    ↓
双腿自动下单
```

生产中心参数见 `config/afuture.live.example.toml`：

| 参数 | 当前值 |
|---|---:|
| `lookback` | 25 |
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

研究按每个自然日最后一根 60 分钟真实 K 线形成日样本，通常对应中国时间 23:00；实盘在 22:55-23:00 使用同步盘口，保留几分钟执行缓冲。

## 两年真实数据证据

主验收使用 AKShare 的新浪**具体交割合约**历史接口，不使用连续合约或随机合成数据。

最近窗口：

```text
2024-08-21 ~ 2026-08-20
484 个交易日
15 个商品期货品种
约 9.9 万根具体合约 60 分钟 K 线
```

研究池包括：

```text
A C EG FG I M MA OI P PP RB RM SA TA Y
```

同时增加独立历史反证窗口：

```text
2022-08-22 ~ 2024-08-20
```

最近两年严格切分：

```text
Train       2024-08-21 ~ 2025-08-20
Validation  2025-08-21 ~ 2026-02-20
Final OOS   2026-02-21 ~ 2026-08-20
```

所有品种和参数资格都在 Train + Validation 固定，Final OOS 只负责否决。

在 **2x 保守往返成本** 下，中心方案的关键历史结果：

| 验收 | 合格品种 | 交易数 | 累计 R | 最大回撤 R | 胜率 |
|---|---|---:|---:|---:|---:|
| 2022-2024 资格 → 下一年 Forward | A | 4 | +0.193 | -0.062 | 50% |
| Train + Validation → Final OOS | M、OI | 4 | +1.208 | -0.042 | 75% |
| 最近两年 M/OI 全窗口，仅作描述 | M、OI | 15 | +7.588 | -0.182 | 86.7% |

对 8 个核心参数各做上下单变量扰动，共 16 组邻域，9/16 同时通过两轮前向测试，通过率 56.25%。因此当前中心点不是单一历史峰值，但仍不代表未来必然盈利。

完整方法、数据边界、风险预算代理和验收条件见：

- `docs/two_year_real_data_validation.md`

可重复研究门：

```bash
python tools/fetch_two_year_60m_universe.py
python tools/fetch_prior_two_year_60m_universe.py
python tools/evaluate_daily_relative_strategy.py
```

GitHub Actions 的 `research-2y` 会自动重新拉取真实数据并执行这些门。任一数据覆盖、OOS、回撤、2x 成本或参数邻域条件失败，工作流直接失败。

## 自动选标与自动交易

生产自动层保持很小：

```text
CTP 合约目录
  ↓
M / OI 白名单
  ↓
过滤临近到期合约
  ↓
每个品种取前 3 个可交易月份
  ↓
生成相邻月份 1-2、2-3
  ↓
恢复持久化的 25 日统计历史
  ↓
relative-value + confirmation + quality gates
  ↓
实时成交量 / OI / bid-ask / 深度 / Net Edge
  ↓
每品种最多 1 个，账户最多 2 个组合
  ↓
CalendarSpreadStrategy
  ↓
RiskManager
  ↓
PairExecutor
  ↓
CTP
```

已有持仓不会因为候选排名变化被强制轮换；如果某组合不再通过资格门，只是在平仓后退役，不会被自动重新开仓。

### 重启一致性

项目面向每天手动启动的个人场景。为避免每天重启都丢失 25 日统计窗口，系统会持久化：

- 自动候选采样历史 `auto_history`
- rolling signal/raw history
- confirmation armed state
- 当前策略仓位状态
- 权益高水位和交易日状态

状态文件使用 schema version、sequence 和 SHA-256 checksum。首次部署如果没有历史状态，必须自然积累 warm-up；系统不会用合成数据伪造 25 日窗口。

## 实盘风险控制

正式示例的主要上限：

| 规则 | 当前示例 |
|---|---:|
| 最大保证金 / 权益 | 35% |
| 最小可用资金 / 权益 | 55% |
| 单交易日最大亏损 | 1% |
| 权益高水位最大回撤 | 8% |
| 最大同时套利组合 | 2 |
| 单合约最大手数 | 20 |
| 单组合风险预算上限 | 2% |
| 最小一档深度倍数 | 2x |
| 最大 bid/ask 宽度 | 3 ticks |
| 两腿最大时间差 | 2 秒 |
| 临近到期禁开仓 | 5 天 |

`2%` 是候选风险预算上限，不是每笔必然使用 2%。最终手数还会被保证金、可用资金、价差波动、一档深度、单合约上限和 CTP 真实参数进一步限制。

双腿异常状态机：

```text
RUNNING
   │
   ├─ 普通风险/数据异常 ───────────→ HALTED
   │
   └─ 双腿失衡/紧急退出失败 ─────→ REDUCE_ONLY
                                      │
                                      ├─ 撤单 + FAK 只减仓
                                      └─ 风险消除 → HALTED → 人工复核
```

正常双腿报单会优先提交当前可成交深度更薄的一腿，把流动性更好的腿留作第二腿对冲，降低裸腿概率。

## 可成交价格与 Net Edge

研究中的统计偏离不等于实盘可交易利润。生产开仓使用真实盘口：

```text
多价差 = near.ask - far.bid
空价差 = near.bid - far.ask
```

并计算：

```text
Net Edge
= 预期回归收益
- 往返手续费
- 双腿滑点
- 裸腿风险缓冲
```

只有 Net Edge、盘口深度、行情同步、保证金和账户风险同时通过才下单。

## 安装

研究/回放/测试：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

CTP：

```bash
python -m pip install -e ".[live,dev]"
```

当前实盘适配器按 `vnpy 4.4.x` 与 `vnpy_ctp 6.7.11.4` 的接口行为实现；升级上游依赖后应重新执行测试柜台验收。

## 常用命令

校验配置：

```bash
afuture validate --config config/afuture.example.toml
```

历史回放：

```bash
afuture replay --config config/afuture.example.toml --data examples/sample_ticks.csv
```

自动选标回放：

```bash
afuture replay --config config/afuture.auto-replay.example.toml --data examples/auto_sample_ticks.csv
```

扫描候选：

```bash
afuture scan --config config/afuture.example.toml --data examples/research_ticks.csv
```

Walk-forward：

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

## CTP 实盘

复制当前生产候选配置：

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

```bash
afuture live --config config/live.toml --confirm-live
```

启动后会先完成账户、持仓、合约元数据、遗留订单和本地状态对账，再允许进入 `RUNNING`。建议使用独立账户或至少不要让其他程序/人工同时交易相同合约，否则未知成交或持仓漂移会触发 fail-closed。

## 项目结构

```text
afuture/
  auto.py               # 合约发现、候选排名、日频历史恢复
  strategy.py           # relative-value / confirmation / exits
  scanner.py            # 统计质量与候选扫描
  economics.py          # 可成交价差、手续费、滑点、Net Edge
  risk.py               # 账户、市场、动态仓位风控
  execution.py          # 双腿执行、回滚、只减仓修复
  engine.py             # 实盘/回放统一事件链
  state.py              # 可校验持久化状态
  broker/ctp.py         # CTP 实盘适配
  broker/sim.py         # 模拟/保守撮合
  research.py           # Walk-forward / OOS / Stress
```

进一步文档：

- `docs/two_year_real_data_validation.md`
- `docs/architecture.md`
- `docs/live-trading.md`
- `docs/data-and-backtest.md`
- `docs/production-checklist.md`

## 不能由回测替代的最后验证

代码和历史数据不能替代真实柜台环境。正式扩大风险预算之前仍应按顺序完成：

```text
真实历史研究
→ 保守回放
→ CTP 测试柜台
→ 极小真实仓位
→ 多交易日观察
→ 再决定是否提高风险预算
```

尤其需要验证期货公司真实手续费/平今费、保证金、夜盘交易日、部分成交、断线重连和报单流控。高收益是目标，不是软件能够保证的属性。
