# 数据、回放与研究

## 1. 数据层级

### 生产/高质量回放

标准 Tick CSV：

```text
timestamp,symbol,exchange,bid_price,ask_price,last_price,bid_volume,ask_volume,trading_day
```

Auto 正式研究还应包含：

```text
limit_up,limit_down,volume,open_interest
```

`timestamp` 必须带时区；所有中国交易时段按 `Asia/Shanghai` 解释。

### 两年公开真实数据证据

高成本 workflow 使用 AKShare/Sina specific-contract 60 分钟历史：

```text
OHLC + volume + open interest
```

公开源没有完整两年历史 L1 bid/ask/depth，所以这部分只能验证**信号层经济性**。历史成交、盘口深度和队列位置仍是代理，不能冒充真实执行证据。

## 2. Data Quality Gate

```bash
afuture data-check \
  --config config/afuture.auto-replay.example.toml \
  --data path/to/data.csv
```

检查：

- 时间范围、交易日、每日样本；
- 合约/品种覆盖；
- duplicate / out-of-order；
- 无效盘口；
- volume/OI 缺失；
- 日内长断档；
- 每日 Auto pair；
- 单一品种数据集中度。

配置式历史回放可以在 `[[contracts]]` 中提供：

```toml
listing = "2025-09-15"
expiry = "2026-09-15"
```

`listing` 会进入 `ContractInfo`，用于 point-in-time Universe 防前视。

## 3. Replay 与生产事件链

`afuture replay` 复用：

```text
AutoPairManager
→ TradingEngine
→ CalendarSpreadStrategy
→ PortfolioRisk / RiskManager
→ PairExecutor
→ SimBroker
```

关键时间语义：

- 历史行情健康：事件时间；
- 历史裸腿超时：事件时间；
- 历史普通报单限速：信号事件时间；
- 实盘：墙钟/单调时钟；
- SimBroker 延迟旧单由当前 Tick 触发成交时，`trade/order` 回报先于同 Tick 的新策略决策。

因此不会再把数年的历史订单压缩成几秒 CPU 时间，或在 broker 已成交而 Engine 尚未收到 trade 时用同一行情继续生成新策略决定。

## 4. 两年 60m 研究必须与生产对齐

`.github/workflows/research-2y.yml` 使用以下固定规则。

### 4.1 中国期货交易日

夜盘 `>=20:00` 映射到**下一观察到的日盘交易日**。这样周五夜盘自然归入周一/下一实际交易日；样本末尾无法确认后续交易日时 fail-closed。

### 4.2 采样时刻

生产 `daily_sample_window`：

```text
22:55-23:00
```

研究只接受该窗口内两腿**完全相同的 60 分钟 timestamp**。缺腿就缺样本，不允许用 15:00 或其它时间补齐。

### 4.3 当时可见活动度

`visible_volume` 是 mapped futures trading day 内截至采样时刻已经发生的累计成交量。OI 使用同步采样时刻值。

当前冻结研究/生产门：

```text
min_volume = 1000
min_open_interest = 5000
```

### 4.4 Point-in-time Universe

每个期货交易日：

```text
20-day delivery/expiry blackout
→ front 3 eligible contracts
→ adjacent pairs only
```

生产使用 CTP 官方 ExpireDate。公开历史 60m 数据没有官方历史 ExpireDate，研究使用交割月 **15 日**作为保守 proxy，并在报告中显式记录这一近似。

## 5. 单 Pair 与 Auto Portfolio

`afuture accept` 只用于固定 pair 诊断，不能替代最终 Auto 生命周期。

正式 Auto 研究：

```bash
afuture accept-auto ...
```

覆盖：

- Train → Validation → OOS；
- 全局小邻域；
- 1x / 1.5x / 2x 成本；
- leave-one-product-out；
- single-product attribution；
- remove-best-period；
- depth haircut；
- latency / market impact；
- Tick gap / quote skew / activity missing。

研究目标是泛化，不是单一区间历史最高收益。

## 6. 两年 signal-level 经济门

真实 60m workflow 固定：

```text
prior1      2022-08-22~2023-08-20
prior2      2023-08-21~2024-08-20
train       2024-08-21~2025-08-20
validation  2025-08-21~2026-02-20
final OOS   2026-02-21~2026-08-20
```

品种身份是冻结资格规则的**输出**，不能写死为 `A/M/OI` 等名字。

经济门检查：

- prior qualification 非空；
- prior → forward 至少 2 笔且 R > 0；
- current qualification 非空；
- final OOS 至少 3 笔且 R > 0；
- final OOS 回撤 > -0.5R；
- 最近两年至少 10 笔且 R > 2；
- 最近两年回撤 > -0.5R；
- 16 个局部邻域至少 50% 通过。

研究程序正常完成但经济门失败时：

```text
exit code = 2
accepted = false
promotion_reasons = [...]
```

workflow 会保存证据，不把“策略没有 Alpha”误报成“程序坏了”。真正的 Python/数据/方法错误仍返回其它非零退出码并导致 workflow 失败。

## 7. 当前 corrected 结果

详见 [两年真实数据验证结论](two_year_real_data_validation.md)。核心结果：

```text
qualified_prior   = MA
prior-forward     = 4 trades, -1.958R
qualified_current = M
final OOS          = 2 trades, +0.296R
recent two years   = 5 trades, +1.028R
neighbor stability = 0 / 16
2% risk proxy annualized ≈ 1.07%
100% annualized target = NOT MET
```

因此当前策略**没有生产晋级**。

## 8. 参考策略家族

对 basis reversal、basis momentum、log-ratio mean reversion、slow-momentum-fast-reversion、persistence 和 volatility regime 做了预注册筛选。

结果：

```text
11 candidates
family_support = {}
stable_family_found = false
```

未通过的实验能力不进入生产默认。

## 9. 何时继续研究

当前 final OOS 已被观察，不能再作为新功能的 pristine holdout。继续开发需要新增信息，例如：

- 未来新交易日；
- 真实 CTP L1 Shadow；
- 新的可靠历史 L1 数据；
- 测试柜台/小资金真实成交质量。

没有新增证据时，不再扩大历史参数搜索空间，也不通过提高杠杆追求年化 100%。
