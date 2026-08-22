"""Minimal TradingEngine extension for the account-exclusive directional portfolio."""
from __future__ import annotations

from datetime import datetime, timezone

from .engine import TradingEngine
from .models import RuntimeMode, Tick


_ACCOUNT_RISK_REASONS = {
    "daily loss limit reached",
    "drawdown limit reached",
    "margin ratio limit reached",
    "available cash reserve too low",
}


class DirectionalTradingEngine(TradingEngine):
    """Reuse the production engine while delegating directional portfolio lifecycle."""

    def __init__(self, *args, directional_manager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.directional_manager = directional_manager
        self._directional_initialized = False

    def initialize_after_ready(self) -> None:
        super().initialize_after_ready()
        if (
            not self._initialized
            or self.halted
            or self._directional_initialized
        ):
            return
        try:
            self.directional_manager.bootstrap(self._reference_now())
            self._directional_initialized = True
        except Exception as exc:
            self.emergency_stop(
                f"directional initialization failed: {exc}"
            )

    def on_tick(self, tick: Tick) -> None:
        try:
            self.directional_manager.observe(tick)
        except Exception as exc:
            self.emergency_stop(f"directional tick handling failed: {exc}")
            return
        super().on_tick(tick)

    def run_once(self) -> None:
        super().run_once()
        if (
            self.halted
            or not self._initialized
            or not self._directional_initialized
            or self.state.runtime_mode != RuntimeMode.RUNNING.value
        ):
            return
        try:
            result = self.directional_manager.maybe_rebalance(
                self._reference_now()
            )
            if result.action == "risk_off":
                self._record(
                    "directional_rebalance",
                    {
                        "action": result.action,
                        "reason": result.reason,
                        "order_ids": list(result.order_ids),
                    },
                )
                if self.directional_manager.has_risk():
                    self.enter_reduce_only(result.reason or "directional risk-off")
                return
            if result.action not in {"hold", "wait"}:
                self._record(
                    "directional_rebalance",
                    {
                        "action": result.action,
                        "reason": result.reason,
                        "order_ids": list(result.order_ids),
                    },
                )
        except Exception as exc:
            self.emergency_stop(f"directional rebalance failed: {exc}")

    def emergency_stop(self, reason: str) -> None:
        # Calendar-spread behavior stays in TradingEngine. Only account-risk breaches in
        # account-exclusive directional mode transition through REDUCE_ONLY so existing
        # directional exposure is actively removed instead of being stranded by HALTED.
        if (
            reason in _ACCOUNT_RISK_REASONS
            and hasattr(self, "directional_manager")
            and self.directional_manager.has_risk()
        ):
            self.enter_reduce_only(reason)
            return
        super().emergency_stop(reason)

    def stop(self) -> None:
        try:
            self.directional_manager.close()
        finally:
            super().stop()

    def _market_health_reason(self) -> str:
        if self.pairs:
            return super()._market_health_reason()
        required = set(self.directional_manager.required_symbols())
        if not required:
            return ""
        reference = self._health_reference_time()
        if reference is None:
            return ""
        quotes_ready = required.issubset(self.quotes)
        if not quotes_ready and self._quote_initialization_grace_active():
            return ""
        if self.historical_mode:
            max_quote_age = 0.0
        else:
            max_quote_age = self._max_quote_age(
                required, reference, quotes_ready
            )
            if max_quote_age is None:
                return "market quote timestamp is in the future"
        try:
            self.broker.get_account()
            account_ready = True
        except Exception:
            account_ready = False
        try:
            self.broker.get_positions()
            position_ready = True
        except Exception:
            position_ready = False
        return self.health_monitor.evaluate(
            connected=self.broker.is_ready(),
            account_ready=account_ready,
            position_ready=position_ready,
            quotes_ready=quotes_ready,
            max_quote_age=max_quote_age,
        )

    def _reduce_only_cycle(self) -> None:
        if self.directional_manager.has_risk():
            try:
                result = self.directional_manager.flatten(
                    self._reference_now()
                )
                if result.action == "reject" and result.reason:
                    suffix = f"directional flatten failed: {result.reason}"
                    self.state.reduce_reason = (
                        f"{self.state.reduce_reason}; {suffix}"
                        if self.state.reduce_reason
                        else suffix
                    )
                    self._persist()
            except Exception as exc:
                suffix = f"directional flatten exception: {exc}"
                self.state.reduce_reason = (
                    f"{self.state.reduce_reason}; {suffix}"
                    if self.state.reduce_reason
                    else suffix
                )
                self._persist()
            return
        super()._reduce_only_cycle()

    def _reference_now(self) -> datetime:
        reference = self._health_reference_time()
        if reference is None:
            reference = self.health_clock()
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return reference
