from datetime import date, datetime, timezone

import pandas as pd
import pytest

from afuture.directional import DirectionalConfig
from afuture.execution_aligned_runtime import (
    ExecutionAlignedDirectionalPortfolioManager,
    ExecutionAlignedSignalHistory,
    FROZEN_PRODUCTS,
)
from afuture.models import AccountSnapshot
from afuture.risk import RiskConfig, RiskManager


NOW = datetime(2026, 8, 24, 13, 1, tzinfo=timezone.utc)


class _Provider:
    def __init__(self):
        dates = pd.date_range(end="2026-08-21", periods=180, freq="B")
        close = pd.DataFrame({"A": range(100, 280)}, index=dates, dtype=float)
        open_prices = close.shift(1).fillna(close.iloc[0])
        self.history = ExecutionAlignedSignalHistory(open_prices, close)
        self.fail = False

    def load(self, products):
        if self.fail:
            raise RuntimeError("provider unavailable")
        return self.history


class _Policy:
    def __init__(self):
        self.calls = 0

    def target_weights(self, open_prices, close):
        self.calls += 1
        assert open_prices.index.equals(close.index)
        assert open_prices.index[-1] > self._last_observed(close)
        return {"A": 1.0}

    @staticmethod
    def _last_observed(close):
        return close.index[-2]


class _Broker:
    def is_ready(self):
        return True

    def get_account(self):
        return AccountSnapshot(
            balance=100000,
            equity=100000,
            available=100000,
            margin=0,
            realized_pnl=0,
            unrealized_pnl=0,
            trading_day="20260825",
        )

    def get_positions(self):
        return []

    def get_active_orders(self):
        return []


def _manager(provider=None, policy=None):
    return ExecutionAlignedDirectionalPortfolioManager(
        DirectionalConfig(
            enabled=True,
            products=("A",),
            exchanges=("DCE",),
            signal_max_age_hours=120.0,
        ),
        _Broker(),
        RiskManager(RiskConfig()),
        signal_provider=provider or _Provider(),
        policy=policy or _Policy(),
    )


def test_execution_aligned_runtime_passes_open_and_close_history_to_policy():
    policy = _Policy()
    manager = _manager(policy=policy)
    history = manager._load_signal(NOW)
    weights = manager._next_target_weights(history)
    assert weights == {"A": 1.0}
    assert policy.calls == 1


def test_signal_freshness_uses_completed_trading_day_not_only_hour_age():
    manager = _manager()
    # Friday's completed bar is valid for the first post-weekend trading session.
    history = manager._load_signal(NOW, required_signal_day=date(2026, 8, 21))
    assert history.close.index[-1].date() == date(2026, 8, 21)

    # Missing a required normal completed trading day must fail even though 120h has not expired.
    with pytest.raises(RuntimeError, match="required signal trading day"):
        manager._load_signal(NOW, required_signal_day=date(2026, 8, 24))


def test_cached_signal_can_cover_transient_provider_failure_when_required_day_is_present():
    provider = _Provider()
    manager = _manager(provider=provider)
    manager._load_signal(NOW, required_signal_day=date(2026, 8, 21))
    provider.fail = True
    later = datetime(2026, 8, 25, 13, 1, tzinfo=timezone.utc)
    cached = manager._load_signal(later, required_signal_day=date(2026, 8, 21))
    assert cached.close.index[-1].date() == date(2026, 8, 21)


def test_default_execution_aligned_runtime_requires_the_frozen_50_product_universe():
    assert len(FROZEN_PRODUCTS) == 50
    with pytest.raises(ValueError, match="frozen 50-product universe"):
        ExecutionAlignedDirectionalPortfolioManager(
            DirectionalConfig(
                enabled=True,
                products=("A", "M"),
                exchanges=("DCE",),
            ),
            _Broker(),
            RiskManager(RiskConfig()),
            signal_provider=_Provider(),
        )
