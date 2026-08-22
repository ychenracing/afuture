"""Runtime lifecycle for the frozen aggressive directional portfolio.

The manager is account-exclusive and deliberately stateless about fills/positions: the
broker remains the sole account/order/position truth. It computes causal daily target
weights, selects current concrete contracts by point-in-time activity, reconciles target
lots against broker positions, reduces risk first, and only then submits new FAK risk
through the shared RiskManager gates.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, time, timezone
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from .directional import (
    DirectionalConfig,
    DirectionalContractSelector,
    FrozenAggressivePolicy,
    build_rebalance_plan,
    build_target_lots,
)
from .models import (
    ContractInfo,
    ContractPosition,
    ContractSpec,
    Offset,
    OrderRequest,
    OrderSide,
    OrderType,
    Tick,
)
from .position import PositionBook
from .risk import OrderRateLimiter, RiskManager


_CHINA_TZ = ZoneInfo("Asia/Shanghai")


class DirectionalSignalProvider(Protocol):
    def load(self, products: tuple[str, ...]) -> pd.DataFrame: ...


class SinaContinuousSignalProvider:
    """Load public continuous daily closes used by the frozen production signal."""

    def __init__(self, max_workers: int = 8) -> None:
        self.max_workers = max(1, int(max_workers))

    @staticmethod
    def _load_one(product: str) -> pd.Series:
        try:
            import akshare as ak
        except ImportError as exc:  # live extra owns this dependency
            raise RuntimeError(
                "directional live mode requires the 'live' extra with akshare"
            ) from exc
        frame = ak.futures_zh_daily_sina(symbol=f"{product.upper()}0").copy()
        if frame.empty or not {"date", "close"}.issubset(frame.columns):
            raise RuntimeError(f"continuous signal history unavailable: {product}")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["date", "close"])
        frame = frame[frame["close"] > 0]
        frame.drop_duplicates("date", keep="last", inplace=True)
        frame.sort_values("date", inplace=True)
        if frame.empty:
            raise RuntimeError(f"continuous signal history empty: {product}")
        return frame.set_index("date")["close"].rename(product.upper())

    def load(self, products: tuple[str, ...]) -> pd.DataFrame:
        unique = tuple(dict.fromkeys(item.upper() for item in products))
        series: dict[str, pd.Series] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, max(len(unique), 1))
        ) as executor:
            futures = {
                executor.submit(self._load_one, product): product
                for product in unique
            }
            for future in as_completed(futures):
                product = futures[future]
                try:
                    series[product] = future.result()
                except Exception as exc:
                    errors.append(f"{product}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError(
                "directional signal refresh failed: " + "; ".join(sorted(errors))
            )
        frame = pd.concat([series[product] for product in unique], axis=1).sort_index()
        return frame


@dataclass(frozen=True)
class DirectionalActionResult:
    action: str
    reason: str = ""
    order_ids: tuple[str, ...] = ()


class DirectionalPortfolioManager:
    """Translate the frozen signal into concrete, risk-gated target positions."""

    def __init__(
        self,
        config: DirectionalConfig,
        broker,
        risk_manager: RiskManager,
        *,
        signal_provider: DirectionalSignalProvider | None = None,
        policy=None,
        aggressive_ticks: int = 1,
        close_today_first: bool = False,
        metadata_timeout_seconds: float = 10.0,
        static_specs: Mapping[str, ContractSpec] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.broker = broker
        self.risk_manager = risk_manager
        self.selector = DirectionalContractSelector(config)
        self.signal_provider = signal_provider or SinaContinuousSignalProvider()
        self.policy = policy or FrozenAggressivePolicy(
            products=tuple(item.upper() for item in config.products)
        )
        self.aggressive_ticks = max(0, int(aggressive_ticks))
        self.close_today_first = bool(close_today_first)
        self.metadata_timeout_seconds = max(float(metadata_timeout_seconds), 0.1)
        self.rate_limiter = OrderRateLimiter(
            risk_manager.config.max_orders_per_minute
        )
        self._catalog: list[ContractInfo] = []
        self._ticks: dict[str, Tick] = {}
        self._specs: dict[str, ContractSpec] = dict(static_specs or {})
        self._signal_frame: pd.DataFrame | None = None
        self._signal_refresh_date = None
        self._initialized = False

    def bootstrap(self, now: datetime) -> None:
        catalog = self.broker.get_contract_catalog()
        if not catalog:
            raise RuntimeError("directional contract catalog is empty")
        products = {item.upper() for item in self.config.products}
        exchanges = {item.upper() for item in self.config.exchanges}
        local_date = self._local(now).date()
        allowed: list[ContractInfo] = []
        for item in catalog:
            if item.product.upper() not in products:
                continue
            if item.exchange.upper() not in exchanges:
                continue
            if item.listing:
                try:
                    if datetime.fromisoformat(item.listing).date() > local_date:
                        continue
                except ValueError:
                    continue
            allowed.append(item)
        if not allowed:
            raise RuntimeError("directional contract catalog has no allowed products")
        self._catalog = allowed
        for item in allowed:
            self.broker.subscribe(item.symbol, item.exchange)
        self._initialized = True

    def close(self) -> None:
        closer = getattr(self.signal_provider, "close", None)
        if callable(closer):
            closer()

    def observe(self, tick: Tick) -> None:
        if not self._initialized:
            return
        allowed = {item.symbol for item in self._catalog}
        if tick.symbol in allowed:
            self._ticks[tick.symbol] = tick

    def maybe_rebalance(self, now: datetime) -> DirectionalActionResult:
        if not self._initialized:
            return DirectionalActionResult("reject", "directional manager is not initialized")
        if not self.broker.is_ready():
            return DirectionalActionResult("reject", "broker is not ready")
        if not self._inside_rebalance_window(now):
            return DirectionalActionResult("hold", "outside directional rebalance window")
        if self.broker.get_active_orders():
            return DirectionalActionResult("wait", "active orders must settle before rebalance")

        try:
            signal = self._load_signal(now)
            target_weights = self._next_target_weights(signal)
        except Exception as exc:
            return DirectionalActionResult("reject", f"directional signal unavailable: {exc}")

        local_date = self._local(now).date()
        selected = self.selector.select(self._catalog, self._ticks, local_date)
        required_products = {
            product.upper()
            for product, weight in target_weights.items()
            if abs(float(weight)) > 1e-15
        }
        missing = sorted(required_products - set(selected))
        if missing:
            return DirectionalActionResult(
                "reject", f"no eligible concrete contract for products: {missing}"
            )

        positions = self.broker.get_positions()
        symbols = {
            item.symbol for item in positions if not item.empty
        } | {selected[product].symbol for product in required_products}
        try:
            specs = self._ensure_specs(symbols)
        except Exception as exc:
            return DirectionalActionResult("reject", f"directional metadata unavailable: {exc}")

        product_ticks = {
            product: self._ticks[selected[product].symbol]
            for product in required_products
        }
        account = self.broker.get_account()
        target_lots = build_target_lots(
            account,
            {product: target_weights[product] for product in required_products},
            product_ticks,
            specs,
            max_contract_volume=min(
                self.config.max_contract_volume,
                self.risk_manager.config.max_contract_volume,
            ),
        )
        plan = build_rebalance_plan(positions, target_lots)
        if plan.reductions:
            return self._submit_reductions(
                positions, plan.reductions, now, reference="directional:rebalance"
            )
        if not plan.openings:
            return DirectionalActionResult("hold", "directional portfolio is at target")
        return self._submit_openings(
            positions,
            plan.openings,
            selected,
            specs,
            now,
        )

    def flatten(self, now: datetime) -> DirectionalActionResult:
        if self.broker.get_active_orders():
            return DirectionalActionResult("wait", "active orders must settle before flatten")
        positions = self.broker.get_positions()
        plan = build_rebalance_plan(positions, {})
        if not plan.reductions:
            return DirectionalActionResult("hold", "directional portfolio is flat")
        return self._submit_reductions(
            positions, plan.reductions, now, reference="directional:flatten"
        )

    def has_risk(self) -> bool:
        return any(not position.empty for position in self.broker.get_positions())

    def required_symbols(self) -> set[str]:
        symbols = {
            position.symbol
            for position in self.broker.get_positions()
            if not position.empty
        }
        symbols.update(self._ticks)
        return symbols

    def _load_signal(self, now: datetime) -> pd.DataFrame:
        local = self._local(now)
        if (
            self._signal_frame is None
            or self._signal_refresh_date != local.date()
        ):
            frame = self.signal_provider.load(
                tuple(item.upper() for item in self.config.products)
            ).copy()
            frame.index = pd.to_datetime(frame.index, errors="coerce")
            frame = frame[~frame.index.isna()].sort_index()
            frame.columns = [str(item).upper() for item in frame.columns]
            frame = frame.loc[frame.index.normalize() <= pd.Timestamp(local.date())]
            frame = frame.dropna(how="all")
            if len(frame) < 140:
                raise RuntimeError("directional signal history is shorter than 140 days")
            latest = pd.Timestamp(frame.index[-1]).to_pydatetime().replace(
                tzinfo=_CHINA_TZ
            )
            age_hours = (local - latest).total_seconds() / 3600.0
            if age_hours < -1:
                raise RuntimeError("directional signal history is from the future")
            if age_hours > self.config.signal_max_age_hours:
                raise RuntimeError(
                    f"directional signal history is stale by {age_hours:.1f}h"
                )
            self._signal_frame = frame
            self._signal_refresh_date = local.date()
        return self._signal_frame.copy()

    def _next_target_weights(self, close: pd.DataFrame) -> dict[str, float]:
        last = pd.Timestamp(close.index[-1])
        synthetic_index = last + pd.offsets.BDay(1)
        synthetic = close.iloc[[-1]].copy()
        synthetic.index = pd.DatetimeIndex([synthetic_index])
        extended = pd.concat([close, synthetic])
        weights = self.policy.target_weights(extended)
        gross = sum(abs(float(value)) for value in weights.values())
        if gross > self.config.max_gross_leverage + 1e-10:
            raise RuntimeError(
                f"directional signal exceeds configured gross leverage: {gross:.6f}"
            )
        return {str(key).upper(): float(value) for key, value in weights.items()}

    def _ensure_specs(self, symbols: set[str]) -> dict[str, ContractSpec]:
        missing = sorted(symbol for symbol in symbols if symbol not in self._specs)
        if missing:
            getter = getattr(self.broker, "get_live_contract_specs", None)
            if getter is None:
                raise RuntimeError(f"missing contract metadata: {missing}")
            fetched = getter(
                missing, timeout_seconds=self.metadata_timeout_seconds
            )
            self._specs.update(fetched)
        still_missing = sorted(symbol for symbol in symbols if symbol not in self._specs)
        if still_missing:
            raise RuntimeError(f"missing contract metadata: {still_missing}")
        return {symbol: self._specs[symbol] for symbol in symbols}

    def _submit_reductions(
        self,
        positions: list[ContractPosition],
        reductions: Mapping[str, int],
        now: datetime,
        *,
        reference: str,
    ) -> DirectionalActionResult:
        book = PositionBook(positions)
        position_map = {item.symbol: item for item in positions}
        order_ids: list[str] = []
        for symbol, delta in sorted(reductions.items()):
            position = position_map.get(symbol)
            tick = self._ticks.get(symbol)
            spec = self._specs.get(symbol)
            if position is None or tick is None:
                return DirectionalActionResult(
                    "reject", f"missing reduction quote/position for {symbol}"
                )
            if spec is None:
                try:
                    spec = self._ensure_specs({symbol})[symbol]
                except Exception as exc:
                    return DirectionalActionResult("reject", str(exc))
            quote = self.risk_manager.check_quotes([tick], now)
            if not quote.allowed:
                return DirectionalActionResult("reject", quote.reason)
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            try:
                children = book.plan_close(
                    symbol,
                    position.exchange,
                    side,
                    abs(int(delta)),
                    close_today_first=self.close_today_first,
                    price=self._aggressive_price(tick, spec, side),
                    reference=reference,
                )
            except ValueError as exc:
                return DirectionalActionResult("reject", str(exc))
            for child in children:
                if not self.rate_limiter.allow(now.timestamp()):
                    return DirectionalActionResult(
                        "reject", "order rate limit reached"
                    )
                try:
                    order_ids.append(
                        self.broker.send_order(
                            replace(child, order_type=OrderType.FAK)
                        )
                    )
                except Exception as exc:
                    return DirectionalActionResult(
                        "reject", f"directional reduction submission failed: {exc}", tuple(order_ids)
                    )
        return DirectionalActionResult("reduce", order_ids=tuple(order_ids))

    def _submit_openings(
        self,
        positions: list[ContractPosition],
        openings: Mapping[str, int],
        selected: Mapping[str, ContractInfo],
        specs: Mapping[str, ContractSpec],
        now: datetime,
    ) -> DirectionalActionResult:
        selected_by_symbol = {item.symbol: item for item in selected.values()}
        requests: list[OrderRequest] = []
        for symbol, delta in sorted(openings.items()):
            item = selected_by_symbol.get(symbol)
            tick = self._ticks.get(symbol)
            spec = specs.get(symbol)
            if item is None or tick is None or spec is None:
                return DirectionalActionResult(
                    "reject", f"missing opening quote/metadata for {symbol}"
                )
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            volume = abs(int(delta))
            quote = self.risk_manager.check_quotes([tick], now)
            if not quote.allowed:
                return DirectionalActionResult("reject", quote.reason)
            market = self.risk_manager.check_contract_entry(
                tick,
                side,
                requested_volume=volume,
                spec=spec,
                session_windows=(self.config.rebalance_window,),
            )
            if not market.allowed:
                return DirectionalActionResult("reject", market.reason)
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    exchange=item.exchange,
                    side=side,
                    offset=Offset.OPEN,
                    volume=volume,
                    price=self._aggressive_price(tick, spec, side),
                    order_type=OrderType.FAK,
                    reference=f"directional:{item.product.upper()}",
                )
            )

        current_volumes = {
            position.symbol: position.long_total + position.short_total
            for position in positions
        }
        account_decision = self.risk_manager.check_open_orders(
            self.broker.get_account(),
            requests,
            dict(specs),
            current_contract_volumes=current_volumes,
        )
        if not account_decision.allowed:
            return DirectionalActionResult("reject", account_decision.reason)

        order_ids: list[str] = []
        for request in requests:
            if not self.rate_limiter.allow(now.timestamp()):
                return DirectionalActionResult(
                    "reject", "order rate limit reached", tuple(order_ids)
                )
            try:
                order_ids.append(self.broker.send_order(request))
            except Exception as exc:
                return DirectionalActionResult(
                    "reject",
                    f"directional opening submission failed: {exc}",
                    tuple(order_ids),
                )
        return DirectionalActionResult("open", order_ids=tuple(order_ids))

    def _inside_rebalance_window(self, timestamp: datetime) -> bool:
        local_time = self._local(timestamp).timetz().replace(tzinfo=None)
        start_raw, end_raw = self.config.rebalance_window.split("-", 1)
        start = time.fromisoformat(start_raw)
        end = time.fromisoformat(end_raw)
        if start <= end:
            return start <= local_time <= end
        return local_time >= start or local_time <= end

    def _aggressive_price(
        self, tick: Tick, spec: ContractSpec, side: OrderSide
    ) -> float:
        if side is OrderSide.BUY:
            price = tick.ask_price + self.aggressive_ticks * spec.price_tick
            return min(price, tick.limit_up) if tick.limit_up > 0 else price
        price = tick.bid_price - self.aggressive_ticks * spec.price_tick
        return max(price, tick.limit_down) if tick.limit_down > 0 else price

    @staticmethod
    def _local(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=_CHINA_TZ)
        return timestamp.astimezone(_CHINA_TZ)
