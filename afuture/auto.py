"""轻量级自动合约发现与跨期组合选择。

这个模块只解决“从哪里交易”这一件事：从 CTP 合约目录中生成同品种相邻月份，
观察实时盘口后按流动性、成交量、持仓量、均值回归和 Net Edge 排名。
它不创建第二套策略、风控或执行系统，选中的组合仍交给 ``TradingEngine`` 处理。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from .models import ContractInfo, ContractSpec, PairConfig, Tick
from .scanner import SpreadScanner


@dataclass(frozen=True)
class AutoConfig:
    """自动发现的最小配置。

    默认不自动启用；实盘示例会显式开启。``products`` 是品种白名单，避免个人程序
    一次订阅全市场数百个合约，把复杂度和 CTP 流控风险无意义放大。
    """

    enabled: bool = False
    products: tuple[str, ...] = ("m", "rb", "TA", "c", "p")
    exchanges: tuple[str, ...] = ("DCE", "SHFE", "CZCE")
    max_active_pairs: int = 2
    max_pairs_per_product: int = 1
    max_contracts_per_product: int = 3
    min_days_to_expiry: int = 20
    scan_interval_seconds: float = 30.0
    max_sync_seconds: float = 2.0

    lookback: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_pair_volume: int = 3
    sample_seconds: int = 60
    max_holding_samples: int = 120
    structural_mean_shift_z: float = 3.0
    structural_vol_ratio: float = 2.5
    legging_buffer: float = 0.0

    min_volume: float = 5000.0
    min_open_interest: float = 10000.0
    min_liquidity_score: float = 0.5
    min_stationarity_score: float = 0.02
    max_half_life: float = 120.0
    min_net_edge: float = 0.0
    slippage_ticks: int = 1
    metadata_timeout_seconds: float = 10.0
    session_windows: tuple[str, ...] = (
        "09:00-10:15",
        "10:30-11:30",
        "13:30-15:00",
    )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.products or not self.exchanges:
            raise ValueError("auto products/exchanges cannot be empty")
        if self.max_active_pairs <= 0 or self.max_pairs_per_product <= 0:
            raise ValueError("auto pair limits must be positive")
        if self.max_contracts_per_product < 2:
            raise ValueError("auto max_contracts_per_product must be at least 2")
        if self.min_days_to_expiry < 0 or self.scan_interval_seconds < 0:
            raise ValueError("auto calendar/scan limits cannot be negative")
        if self.max_sync_seconds <= 0:
            raise ValueError("auto max_sync_seconds must be positive")
        if self.lookback < 2 or not 0 <= self.exit_z < self.entry_z < self.stop_z:
            raise ValueError("auto z-score thresholds are invalid")
        if self.max_pair_volume <= 0:
            raise ValueError("auto max_pair_volume must be positive")
        if self.min_volume < 0 or self.min_open_interest < 0:
            raise ValueError("auto activity thresholds cannot be negative")
        if not 0 <= self.min_liquidity_score <= 1:
            raise ValueError("auto min_liquidity_score must be between 0 and 1")
        if not 0 <= self.min_stationarity_score <= 1:
            raise ValueError("auto min_stationarity_score must be between 0 and 1")
        if self.max_half_life <= 0 or self.metadata_timeout_seconds <= 0:
            raise ValueError("auto half-life/metadata timeout must be positive")


class AutoPairSelector:
    """把合约目录转换为少量、可解释的相邻月份候选。"""

    def __init__(self, config: AutoConfig) -> None:
        config.validate()
        self.config = config

    def build_pairs(
        self, catalog: Iterable[ContractInfo], today: date
    ) -> list[PairConfig]:
        allowed_products = {item.lower() for item in self.config.products}
        allow_all_products = "*" in allowed_products
        allowed_exchanges = {item.upper() for item in self.config.exchanges}
        grouped: dict[tuple[str, str], list[tuple[date, ContractInfo]]] = defaultdict(list)

        for item in catalog:
            product_key = item.product.lower()
            if not allow_all_products and product_key not in allowed_products:
                continue
            if item.exchange.upper() not in allowed_exchanges:
                continue
            try:
                expiry = date.fromisoformat(item.expiry)
            except ValueError:
                continue
            if (expiry - today).days < self.config.min_days_to_expiry:
                continue
            grouped[(product_key, item.exchange.upper())].append((expiry, item))

        pairs: list[PairConfig] = []
        for (product, exchange), rows in sorted(grouped.items()):
            rows.sort(key=lambda row: (row[0], row[1].symbol))
            rows = rows[: self.config.max_contracts_per_product]
            for (_, near), (_, far) in zip(rows, rows[1:]):
                pairs.append(self._pair(product, exchange, near, far))
        return pairs

    def _pair(
        self,
        product: str,
        exchange: str,
        near: ContractInfo,
        far: ContractInfo,
    ) -> PairConfig:
        pair_id = f"auto_{product}_{near.symbol}_{far.symbol}"
        return PairConfig(
            pair_id=pair_id,
            near_symbol=near.symbol,
            far_symbol=far.symbol,
            exchange=exchange,
            volume=self.config.max_pair_volume,
            lookback=self.config.lookback,
            entry_z=self.config.entry_z,
            exit_z=self.config.exit_z,
            stop_z=self.config.stop_z,
            sample_seconds=self.config.sample_seconds,
            expiry_near=near.expiry,
            expiry_far=far.expiry,
            max_holding_samples=self.config.max_holding_samples,
            structural_mean_shift_z=self.config.structural_mean_shift_z,
            structural_vol_ratio=self.config.structural_vol_ratio,
            min_net_edge=self.config.min_net_edge,
            legging_buffer=self.config.legging_buffer,
            risk_group=product,
            session_windows=self.config.session_windows,
        )


class AutoPairManager:
    """维护自动候选的行情历史、评分缓存和激活集合。"""

    def __init__(self, config: AutoConfig) -> None:
        config.validate()
        self.config = config
        self.selector = AutoPairSelector(config)
        self.scanner = SpreadScanner(
            min_liquidity_score=config.min_liquidity_score,
            slippage_ticks=config.slippage_ticks,
            max_sync_seconds=config.max_sync_seconds,
        )
        self.candidate_pairs: list[PairConfig] = []
        self._pairs: dict[str, PairConfig] = {}
        self._history: dict[str, deque[Tick]] = {}
        self._specs: dict[str, ContractSpec] = {}
        self._initialized = False
        self._last_scan: datetime | None = None
        self._catalog_day: date | None = None
        self.last_eligible_ids: set[str] = set()

    @property
    def initialized(self) -> bool:
        return self._initialized

    def prepare_catalog(
        self, catalog: Iterable[ContractInfo], today: date
    ) -> list[PairConfig]:
        self.candidate_pairs = self.selector.build_pairs(catalog, today)
        self._pairs = {pair.pair_id: pair for pair in self.candidate_pairs}
        self._catalog_day = today
        max_history = max(self.config.lookback * 4, self.config.lookback + 8)
        for pair in self.candidate_pairs:
            for symbol in (pair.near_symbol, pair.far_symbol):
                self._history.setdefault(symbol, deque(maxlen=max_history))
        return list(self.candidate_pairs)

    def bootstrap(
        self,
        broker,
        today: date,
        restored_pairs: dict[str, dict] | None = None,
    ) -> list[tuple[PairConfig, dict[str, ContractSpec]]]:
        """读取柜台目录、订阅候选，并恢复上次仍需管理的动态组合。"""
        catalog = broker.get_contract_catalog()
        if not catalog:
            raise RuntimeError("auto discovery contract catalog is empty")
        self.prepare_catalog(catalog, today)

        restored: list[tuple[PairConfig, dict[str, ContractSpec]]] = []
        catalog_symbols = {item.symbol for item in catalog}
        for pair_id, raw in (restored_pairs or {}).items():
            row = dict(raw)
            if "session_windows" in row:
                row["session_windows"] = tuple(row["session_windows"])
            pair = PairConfig(**row)
            if pair.near_symbol not in catalog_symbols or pair.far_symbol not in catalog_symbols:
                raise RuntimeError(f"persisted auto pair is no longer in CTP catalog: {pair_id}")
            self._pairs[pair.pair_id] = pair
            if all(existing.pair_id != pair.pair_id for existing in self.candidate_pairs):
                self.candidate_pairs.append(pair)
            pair_specs = self._ensure_specs(broker, pair)
            restored.append((pair, pair_specs))

        symbols = {
            symbol
            for pair in self.candidate_pairs
            for symbol in (pair.near_symbol, pair.far_symbol)
        }
        for symbol in sorted(symbols):
            pair = next(
                p
                for p in self.candidate_pairs
                if symbol in {p.near_symbol, p.far_symbol}
            )
            broker.subscribe(symbol, pair.exchange)
        self._initialized = True
        return restored

    def refresh_if_needed(
        self,
        broker,
        today: date,
        *,
        retained_pairs: Iterable[PairConfig] = (),
    ) -> bool:
        """交易日变化时重新应用到期过滤并订阅新进入前排的合约。"""
        if self._catalog_day == today:
            return False
        # 保证金/手续费可能跨交易日调整，不能把前一交易日的 CTP 查询结果永久缓存。
        self._specs.clear()
        catalog = broker.get_contract_catalog()
        if not catalog:
            raise RuntimeError("auto discovery contract catalog is empty")
        fresh = self.selector.build_pairs(catalog, today)
        retained = {pair.pair_id: pair for pair in retained_pairs}
        self.candidate_pairs = list(fresh)
        self._pairs = {pair.pair_id: pair for pair in fresh}
        self._pairs.update(retained)
        for pair in retained.values():
            if all(item.pair_id != pair.pair_id for item in self.candidate_pairs):
                self.candidate_pairs.append(pair)
        max_history = max(self.config.lookback * 4, self.config.lookback + 8)
        symbols = {
            symbol
            for pair in self.candidate_pairs
            for symbol in (pair.near_symbol, pair.far_symbol)
        }
        for symbol in symbols:
            self._history.setdefault(symbol, deque(maxlen=max_history))
            pair = next(
                item
                for item in self.candidate_pairs
                if symbol in {item.near_symbol, item.far_symbol}
            )
            broker.subscribe(symbol, pair.exchange)
        self._catalog_day = today
        self._last_scan = None
        return True

    def observe(self, tick: Tick) -> None:
        """按策略采样周期保留行情，避免高频原始 Tick 挤掉统计时间窗口。"""
        history = self._history.get(tick.symbol)
        if history is None:
            return
        sample_seconds = self.config.sample_seconds
        if sample_seconds <= 0 or not history:
            history.append(tick)
            return

        current_bucket = int(tick.timestamp.timestamp() // sample_seconds)
        last_bucket = int(history[-1].timestamp.timestamp() // sample_seconds)
        if current_bucket < last_bucket:
            # CTP 偶发乱序回报不应倒退统计窗口。
            return
        if current_bucket == last_bucket:
            history[-1] = tick
            return
        history.append(tick)

    def should_scan(self, now: datetime) -> bool:
        if self._last_scan is None:
            return True
        return (now - self._last_scan).total_seconds() >= self.config.scan_interval_seconds

    def select(
        self,
        broker,
        *,
        now: datetime,
        protected_pair_ids: set[str],
    ) -> list[PairConfig] | None:
        """按硬门筛选并排序；已有持仓组合优先占用名额，避免为轮换强平。"""
        if not self._initialized or not self.should_scan(now):
            return None
        self._last_scan = now

        scored: list[tuple[PairConfig, float]] = []
        self.last_eligible_ids = set()
        for pair in self.candidate_pairs:
            near_history = self._history.get(pair.near_symbol, ())
            far_history = self._history.get(pair.far_symbol, ())
            if len(near_history) < pair.lookback or len(far_history) < pair.lookback:
                continue
            near = near_history[-1]
            far = far_history[-1]
            if min(near.volume, far.volume) < self.config.min_volume:
                continue
            if min(near.open_interest, far.open_interest) < self.config.min_open_interest:
                continue

            ticks = list(near_history) + list(far_history)
            statistics = self.scanner.statistics(pair, ticks)
            if statistics is None:
                continue
            if self.scanner.entry_signal(pair, near, far, statistics) is None:
                continue
            if statistics.stationarity_score < self.config.min_stationarity_score:
                continue
            if statistics.half_life > self.config.max_half_life:
                continue

            # 只有纯行情统计和可成交阈值都接近开仓时才查询 CTP 保证金/手续费，减少流控和阻塞。
            pair_specs = self._ensure_specs(broker, pair)
            candidate = self.scanner.scan_pair(pair, ticks, pair_specs)
            if candidate is None:
                continue
            if candidate.liquidity_score < self.config.min_liquidity_score:
                continue
            if candidate.net_edge <= self.config.min_net_edge:
                continue
            self.last_eligible_ids.add(pair.pair_id)
            scored.append((pair, candidate.score))

        return self.rank_candidates(scored, protected_pair_ids=protected_pair_ids)

    def rank_candidates(
        self,
        scored: list[tuple[PairConfig, float]],
        *,
        protected_pair_ids: set[str],
    ) -> list[PairConfig]:
        selected: list[PairConfig] = []
        product_counts: dict[str, int] = defaultdict(int)

        for pair_id in sorted(protected_pair_ids):
            pair = self._pairs.get(pair_id)
            if pair is None or len(selected) >= self.config.max_active_pairs:
                continue
            selected.append(pair)
            product_counts[pair.risk_group] += 1

        for pair, _score in sorted(scored, key=lambda row: row[1], reverse=True):
            if len(selected) >= self.config.max_active_pairs:
                break
            if pair.pair_id in {item.pair_id for item in selected}:
                continue
            if product_counts[pair.risk_group] >= self.config.max_pairs_per_product:
                continue
            selected.append(pair)
            product_counts[pair.risk_group] += 1
        return selected

    def pair_specs(self, pair: PairConfig) -> dict[str, ContractSpec]:
        return {
            symbol: self._specs[symbol]
            for symbol in (pair.near_symbol, pair.far_symbol)
        }

    def strategy_seed(self, pair: PairConfig) -> dict:
        """用扫描阶段已经观察到的价差预热策略，避免激活后再等一个 lookback。"""
        ticks = list(self._history.get(pair.near_symbol, ())) + list(
            self._history.get(pair.far_symbol, ())
        )
        synchronized = self.scanner.synchronized_ticks(pair, ticks)
        # 最后一组是触发本轮候选评分的当前行情，不能提前写进历史均值，
        # 否则会把当前极端偏离“稀释”掉，导致激活后策略与 Scanner 判断不一致。
        historical = synchronized[:-1]
        history = [near.mid_price - far.mid_price for near, far in historical]
        if len(history) > pair.lookback:
            history = history[-pair.lookback :]
        last_ts = ""
        if historical:
            last_ts = max(historical[-1][0].timestamp, historical[-1][1].timestamp).isoformat()
        return {
            "history": history,
            "position": 0,
            "entry_mean": 0.0,
            "entry_std": 0.0,
            "last_sample_ts": last_ts,
        }

    def _ensure_specs(
        self, broker, pair: PairConfig
    ) -> dict[str, ContractSpec]:
        missing = [
            symbol
            for symbol in (pair.near_symbol, pair.far_symbol)
            if symbol not in self._specs
        ]
        if missing:
            rows = broker.get_live_contract_specs(
                missing, self.config.metadata_timeout_seconds
            )
            for symbol in missing:
                if symbol not in rows:
                    raise RuntimeError(f"live contract spec missing: {symbol}")
            self._specs.update(rows)
        return self.pair_specs(pair)
