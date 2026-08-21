# 架构与数据流

## 目标

`afuture` 只保留一套正式交易链。Auto、研究、Shadow 和实盘都复用相同的策略、风险和执行对象；旁路模块只负责证据，不获得第二套下单权限。

```text
                 Contract Catalog / Tick
                         │
                         ▼
                  AutoPairManager
       ┌─────────────┬───┴─────────────┐
       │             │                 │
       ▼             ▼                 ▼
 warm samples   candidate evidence   metadata prefetch
       │             │                 │
       └─────────────┴──────┬──────────┘
                            ▼
                    TradingEngine
                            │
                  CalendarSpreadStrategy
                            │
          PortfolioRisk + RiskManager
                            │
                       PairExecutor
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
          SimBroker     ShadowBroker     CtpBroker
                                       (真实订单)

旁路研究：DataQuality → AutoPortfolioRunner → AutoPortfolioAcceptanceGate
旁路证据：candidate / decision / round_trip → ExecutionQualityRecorder
```

## Auto 的职责边界

`AutoPairManager` 只回答“哪个同品种相邻月组合可以获得开仓资格”：

1. CTP/历史合约目录；
2. 品种/交易所白名单；
3. 到期过滤；
4. 每品种前几个有效月份；
5. 相邻月组合；
6. sampled bid/ask、volume、Open Interest、depth；
7. executable Z-score、半衰期、平稳性；
8. 后台预取真实 CTP 保证金/手续费；
9. Net Edge；
10. 少量排名入选组合。

它不直接发订单，也不复制策略和风控。

## 管理权与开仓权

动态组合存在两个不同概念：

- **managed**：系统必须继续拥有并管理已有仓位；
- **open-eligible**：当前 Auto hard gates 仍允许该组合增加新风险。

已有仓位失去 Auto 资格时：

```text
managed = true
open_eligible = false
```

因此组合可以继续正常/紧急退出，但不能在下一次扫描前重新开仓。平仓且无活动订单后立即 unregister。

## 元数据不阻塞 Tick 主循环

CTP 保证金/手续费查询可能需要等待响应。生产 Auto 扫描采用：

```text
统计/可成交阈值预筛
        ↓
metadata request queued
        ↓
当前 Tick 跳过该候选
        ↓
后台单线程完成 CTP query
        ↓
下一次 scan 使用缓存
```

恢复已有动态仓位属于启动安全门，可以同步等待元数据；正常 Tick 关键路径不等待。

交易日变化会使缓存失效并重新获取。

## Warm History

Auto 原始 Tick 先按 `sample_seconds` 桶化。只把有限的 `lookback + buffer` 样本写入：

```text
runtime/market_samples/
```

重启后先 warm-load，再继续收实时样本。不会将高频原始 Tick 或长期历史塞入主状态 JSON。

## 可成交价差

历史统计中心可以使用 mid spread，但交易动作必须使用方向性可成交价差：

```text
LONG entry  = near.ask - far.bid
SHORT entry = near.bid - far.ask
LONG exit   = near.bid - far.ask
SHORT exit  = near.ask - far.bid
```

`PairExecutor` 再次计算 Net Edge，防止研究信号绕过真实成本门。

## 风险与状态真相

`RiskManager` 控制账户、动态手数、市场微观结构和保证金；`PortfolioRiskAnalyzer` 控制 risk group 和滚动相关性。

本地期望持仓只由本进程确认的成交推进，柜台完整持仓快照只用于对账。未知成交、未知活动订单或持仓漂移不会被自动接纳。

异常状态：

```text
RUNNING
  ├─ 普通严重异常 → HALTED
  └─ 裸腿/紧急退出失败 → REDUCE_ONLY → HALTED → 人工复核
```

Kill Switch、高水位、动态 pair 和策略状态都持久化；state envelope 带 schema / sequence / SHA-256 checksum。

## Shadow 的安全边界

`ShadowBroker` 组合：

```text
真实 CTP：catalog / tick / trading_day / metadata / health
本地 SimBroker：account / position / order / trade
```

`ShadowBroker.send_order()` 只调用 `SimBroker.send_order()`，从实现边界上禁止真实报单。每次 Shadow 会话使用独立虚拟账户和独立状态文件。

## 证据链

### Data Quality

`data-check` 在研究前验证原始 CSV 顺序、断档、活动度、合约覆盖和每日 Auto 候选。

### Final Auto Research

`accept-auto` 直接运行最终 Auto 生产链，而不是固定 pair 的替代研究器。参数只由 Train+Validation 的小型全局邻域选择，OOS 只用于验收。

### Robustness

除成本压力外还执行：

- leave-one-product-out；
- single-product attribution；
- remove-best-OOS-period；
- depth haircut；
- latency / market impact；
- data gap；
- cross-leg quote skew；
- volume/OI missing。

### Execution Quality

统一 JSONL 区分：

- `candidate`：Auto 选择证据；
- `decision`：风险与执行决定；
- `round_trip`：预期与实际成交质量。

这样 first divergence 可以定位到 selector / risk / execution，而不是只看到最终账户收益变化。

## 不增加的系统层

个人系统当前不需要数据库、消息队列、Web 服务、微服务或第二交易框架。外部多年份数据可以标准化成 CSV；真实观察使用 Shadow；生产状态仍使用本地 JSON/JSONL。
