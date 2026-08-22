# 数据、回放与研究

## 1. 数据层级

### 生产 / 高质量回放

标准 Tick CSV：

```text
timestamp,symbol,exchange,bid_price,ask_price,last_price,bid_volume,ask_volume,trading_day
```

Auto 研究还应包含：

```text
limit_up,limit_down,volume,open_interest
```

`timestamp` 必须带时区，中国期货交易时段统一按 `Asia/Shanghai` 解释。

### 公开真实历史研究

仓库使用 AKShare/Sina 的 specific-contract 60 分钟或日线 OHLC、成交量和持仓量做信号级验证。公开源没有多年完整 L1 bid/ask/depth，因此这些结果**不能替代**真实盘口 Shadow、测试柜台或实盘成交证据。

## 2. Data Quality Gate

```bash
afuture data-check \
  --config config/afuture.auto-replay.example.toml \
  --data path/to/data.csv
```

检查时间范围、交易日、duplicate/out-of-order、无效盘口、volume/OI 缺失、长断档、每日 Auto pair 和数据集中度。

配置式历史回放可在 `[[contracts]]` 中提供：

```toml
listing = "2025-09-15"
expiry = "2026-09-15"
```

`listing` 会进入 `ContractInfo`，用于 point-in-time Universe 防前视。

## 3. Replay 与生产事件链

`afuture replay` 复用生产边界：

```text
AutoPairManager
→ TradingEngine
→ CalendarSpreadStrategy
→ PortfolioRisk / RiskManager
→ PairExecutor
→ SimBroker
```

关键时间语义：

- 历史行情健康、裸腿超时和普通报单限速使用**事件时间**；
- 实盘使用墙钟/单调时钟；
- SimBroker 延迟旧单由当前 Tick 触发成交时，`trade/order` 回报必须先于同 Tick 的新策略决策。

这样不会把多年历史订单压缩成几秒 CPU 时间，也不会在 broker 已成交而 Engine 尚未收到成交事件时继续用同一行情重复决策。

## 4. corrected 同品种 60 分钟研究

`.github/workflows/research-2y.yml` 是昂贵的手动 L4 milestone gate。

固定窗口：

```text
prior1      2022-08-22~2023-08-20
prior2      2023-08-21~2024-08-20
train       2024-08-21~2025-08-20
validation  2025-08-21~2026-02-20
final OOS   2026-02-21~2026-08-20
```

### 中国期货交易日

夜盘 `>=20:00` 映射到下一观察到的日盘交易日；尾部无法确认后续交易日时 fail-closed。

### 同步采样

冻结的日频规则只接受 `22:55-23:00` 内两腿**完全相同的 60 分钟 timestamp**；缺腿就是缺样本，不用 15:00 或其它时间补齐。

### 当时可见活动度

`visible_volume` 是 mapped futures trading day 内截至采样时刻的累计成交量；OI 使用同步采样时刻值。

```text
min_volume = 1000
min_open_interest = 5000
```

### Point-in-time Universe

```text
20-day delivery/expiry blackout
→ front 3 eligible contracts
→ adjacent pairs only
```

生产使用 CTP 官方 ExpireDate。公开历史 60 分钟源没有完整历史 ExpireDate，研究使用交割月 15 日作为显式保守 proxy。

## 5. 单 Pair 与 Auto Portfolio

`afuture accept` 只用于固定 pair 诊断；正式 Auto 研究使用：

```bash
afuture accept-auto ...
```

覆盖 Train→Validation→OOS、全局小邻域、成本压力、leave-one-product-out、single-product attribution、remove-best-period、depth haircut、latency/market impact、数据 gap、quote skew 和 activity missing。

研究目标是泛化，不是单一区间最高收益。

## 6. corrected M/OI 结果

详见 [最终研究证据结论](research-final-evidence.md) 和 [两年真实数据验证结论](two_year_real_data_validation.md)。核心结果：

```text
prior-forward       = 4 trades, -1.958R
final OOS            = 2 trades, +0.296R
recent two years     = 5 trades, +1.028R
neighbor stability   = 0 / 16
2% risk proxy annualized ≈ 1.07%
100% annualized target = NOT MET
```

所以原 M/OI 规则没有生产晋级。

## 7. 扩展家族的分层研究

### L3 broad economic-pair

约 50 个中国商品期货主连首先用于低成本 broad screen。cross-sectional momentum / slow-fast / reversal / skewness 没有形成稳定家族，因此其实验代码已在最终清理中移除，只保留结论。

随后只保留同交易所、经济关系明确的 pair，使用滚动 beta、残差 Z-score、相关性、OU 半衰期和 volatility regime。资本只分给当前最强的少量合格 pair，并把 gross leverage 封顶在 2x。

`.github/workflows/research-broad.yml` 现在只保留这条有信息价值的经济关系 L3，且为手动 milestone gate。

### L4 specific-contract roll-safe

`.github/workflows/research-specific-pairs.yml` 对 L3 存活关系做具体合约复验：

- 当日 OI/成交量选择具体合约；
- 20 天交割黑窗；
- t 日选择的合约必须同时提供 t 和 t+1 收益；
- 换月时不把不同合约价格跳空拼成 Alpha；
- 30bp 单边压力成本；
- 最多 1 个 pair；
- gross leverage ≤ 2x。

六条预注册关系为 P/Y、PP/V、AL/ZN、BU/FU、CU/AL、J/JM。最终主要收敛到 BU/FU，prior 还包含 PP/V。

已验证压力结果约为：

```text
recent two-year annualized ≈ 4.20%
recent two-year max drawdown ≈ 12.88%
final OOS annualized ≈ 5.78%
final OOS max drawdown ≈ 11.78%
alpha_survives_specific_contract = true
target_met = false
```

这里的 `alpha_survives_specific_contract` 只证明研究信号经受住真实换月语义，不代表跨品种 pair 已接入生产执行。

## 8. 被拒绝并清理的实验

- BU/FU + PP/V 60 分钟 intraday：24 个预注册 profile，**0 个**通过 pre-OOS 门；
- soybean crush、steel/coke margin、polymer/base-metals 等多腿结构：没有形成足够稳定且高收益的家族；
- 失败实验不保留为长期维护代码，只在 `research-final-evidence.md` 和 cleanup inventory 中保留证据与拒绝原因。

## 9. OOS 状态与后续研究

当前 Final OOS 已被多轮研究观察，代码和文档统一标记为 **non-pristine**。它不能再作为新功能的真正未见样本。

后续只有以下新增信息能改变经济结论：

- 新发生、此前未见的交易日；
- 真实 CTP L1 Shadow；
- 更可靠的历史 L1 数据；
- 测试柜台/小资金真实成交质量。

没有新增证据时，不继续扩大历史参数搜索空间，也不通过高杠杆制造年化 100% 的历史数字。

## 10. 验证层级

```text
L1  局部单测 / 因果回归
L2  相关策略或模块 smoke
L3  broad universe / family screen
L4  specific-contract / 完整经济证据门
```

普通小改动不反复触发 L3/L4；最终候选由主 CI 做完整工程验收。
