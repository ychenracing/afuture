"""Thin runtime adapter for the execution-aligned aggressive directional policy."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from .directional_runtime import DirectionalPortfolioManager, _CHINA_TZ
from .execution_aligned_policy import ExecutionAlignedAggressivePolicy


FROZEN_PRODUCTS = (
    "A", "AG", "AL", "AP", "AU", "B", "BC", "BU", "C", "CF", "CJ", "CS",
    "CU", "EB", "EG", "FG", "FU", "HC", "I", "J", "JM", "L", "LH", "LU",
    "M", "MA", "NI", "NR", "OI", "P", "PB", "PF", "PG", "PK", "PP", "RB",
    "RM", "RU", "SA", "SF", "SM", "SN", "SP", "SR", "SS", "TA", "UR", "V",
    "Y", "ZN",
)


@dataclass(frozen=True)
class ExecutionAlignedSignalHistory:
    open: pd.DataFrame
    close: pd.DataFrame


class SinaContinuousOHLCProvider:
    """Load continuous daily open/close history once per production signal refresh."""

    def __init__(self, max_workers: int = 8) -> None:
        self.max_workers = max(1, int(max_workers))

    @staticmethod
    def _load_one(product: str) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError(
                "directional live mode requires the 'live' extra with akshare"
            ) from exc
        frame = ak.futures_zh_daily_sina(symbol=f"{product.upper()}0").copy()
        required = {"date", "open", "close"}
        if frame.empty or not required.issubset(frame.columns):
            raise RuntimeError(f"continuous OHLC history unavailable: {product}")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ("open", "close"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["date", "open", "close"])
        frame = frame[(frame["open"] > 0) & (frame["close"] > 0)]
        frame.drop_duplicates("date", keep="last", inplace=True)
        frame.sort_values("date", inplace=True)
        if frame.empty:
            raise RuntimeError(f"continuous OHLC history empty: {product}")
        return frame.set_index("date")[["open", "close"]]

    def load(self, products: tuple[str, ...]) -> ExecutionAlignedSignalHistory:
        unique = tuple(dict.fromkeys(item.upper() for item in products))
        frames: dict[str, pd.DataFrame] = {}
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
                    frames[product] = future.result()
                except Exception as exc:
                    errors.append(f"{product}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError(
                "directional signal refresh failed: " + "; ".join(sorted(errors))
            )
        open_prices = pd.concat(
            [frames[product]["open"].rename(product) for product in unique], axis=1
        ).sort_index()
        close = pd.concat(
            [frames[product]["close"].rename(product) for product in unique], axis=1
        ).sort_index()
        return ExecutionAlignedSignalHistory(open_prices, close)


class ExecutionAlignedDirectionalPortfolioManager(DirectionalPortfolioManager):
    """Reuse the existing execution lifecycle while supplying OHLC-aware target weights."""

    def __init__(self, config, broker, risk_manager, *, signal_provider=None, policy=None, **kwargs):
        if policy is None:
            configured = tuple(sorted({str(item).upper() for item in config.products}))
            if configured != FROZEN_PRODUCTS:
                raise ValueError(
                    "execution-aligned production requires the frozen 50-product universe"
                )
            policy = ExecutionAlignedAggressivePolicy(products=configured)
        super().__init__(
            config,
            broker,
            risk_manager,
            signal_provider=signal_provider or SinaContinuousOHLCProvider(),
            policy=policy,
            **kwargs,
        )
        self._execution_signal_history: ExecutionAlignedSignalHistory | None = None

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame, max_date: date) -> pd.DataFrame:
        result = frame.copy()
        result.index = pd.to_datetime(result.index, errors="coerce")
        result = result[~result.index.isna()].sort_index()
        result.columns = [str(item).upper() for item in result.columns]
        result = result.loc[result.index.normalize() <= pd.Timestamp(max_date)]
        return result.dropna(how="all")

    def _normalize_history(
        self,
        history: ExecutionAlignedSignalHistory,
        *,
        max_date: date,
    ) -> ExecutionAlignedSignalHistory:
        close = self._normalize_frame(history.close, max_date)
        open_prices = self._normalize_frame(history.open, max_date)
        common = close.index.intersection(open_prices.index)
        close = close.reindex(common)
        open_prices = open_prices.reindex(index=common, columns=close.columns)
        if len(close) < 140:
            raise RuntimeError("directional signal history is shorter than 140 days")
        return ExecutionAlignedSignalHistory(open_prices, close)

    def _validate_signal_history(
        self,
        history: ExecutionAlignedSignalHistory,
        local: datetime,
        required_signal_day: date | None,
    ) -> None:
        latest_day = pd.Timestamp(history.close.index[-1]).date()
        if required_signal_day is not None and latest_day < required_signal_day:
            raise RuntimeError(
                "directional history does not cover required signal trading day "
                f"{required_signal_day.isoformat()}; latest={latest_day.isoformat()}"
            )
        latest = pd.Timestamp(history.close.index[-1]).to_pydatetime().replace(
            tzinfo=_CHINA_TZ
        )
        age_hours = (local - latest).total_seconds() / 3600.0
        if age_hours < -1:
            raise RuntimeError("directional signal history is from the future")
        if age_hours > self.config.signal_max_age_hours:
            raise RuntimeError(
                f"directional signal history is stale by {age_hours:.1f}h"
            )

    def _load_signal(
        self,
        now: datetime,
        required_signal_day: date | None = None,
    ) -> ExecutionAlignedSignalHistory:
        local = self._local(now)
        max_date = required_signal_day or local.date()
        refresh = (
            self._execution_signal_history is None
            or self._signal_refresh_date != local.date()
        )
        if refresh:
            try:
                raw = self.signal_provider.load(
                    tuple(item.upper() for item in self.config.products)
                )
                if not isinstance(raw, ExecutionAlignedSignalHistory):
                    raise RuntimeError(
                        "execution-aligned signal provider must return OHLC history"
                    )
                history = self._normalize_history(raw, max_date=max_date)
                self._execution_signal_history = history
            except Exception:
                if self._execution_signal_history is None:
                    raise
                history = self._normalize_history(
                    self._execution_signal_history,
                    max_date=max_date,
                )
                self._execution_signal_history = history
            self._signal_refresh_date = local.date()

        history = self._execution_signal_history
        if history is None:
            raise RuntimeError("execution-aligned signal history is unavailable")
        history = self._normalize_history(history, max_date=max_date)
        self._validate_signal_history(history, local, required_signal_day)
        self._execution_signal_history = history
        return ExecutionAlignedSignalHistory(history.open.copy(), history.close.copy())

    def _next_target_weights(
        self, history: ExecutionAlignedSignalHistory
    ) -> dict[str, float]:
        close = history.close
        open_prices = history.open
        last = pd.Timestamp(close.index[-1])
        synthetic_index = last + pd.offsets.BDay(1)
        synthetic_close = close.iloc[[-1]].copy()
        synthetic_close.index = pd.DatetimeIndex([synthetic_index])
        synthetic_open = close.iloc[[-1]].copy()
        synthetic_open.index = pd.DatetimeIndex([synthetic_index])
        extended_close = pd.concat([close, synthetic_close])
        extended_open = pd.concat([open_prices, synthetic_open])
        weights = self.policy.target_weights(extended_open, extended_close)
        gross = sum(abs(float(value)) for value in weights.values())
        if gross > self.config.max_gross_leverage + 1e-10:
            raise RuntimeError(
                f"directional signal exceeds configured gross leverage: {gross:.6f}"
            )
        return {
            str(key).upper(): float(value) for key, value in weights.items()
        }
