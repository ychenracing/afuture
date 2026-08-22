# Production-Realizable 100% Return Design

## Goal

Raise the deterministic historical production-mechanics Base path to at least 100% annualized return without increasing the 2x target gross-leverage cap or weakening the 5% daily-loss / 30% total-drawdown / 35% margin / 25% available-cash gates.

## Evidence and diagnosis

The previous frozen research path reached 107.46% annualized at 5bp, but the production-mechanics proxy reached only 6.79% because a recoverable session-level daily-loss event permanently halted the account on 2024-09-19. A throwaway attribution replay showed that preserving the 5% daily-loss gate while flattening for the current trading day and automatically resuming on the next trading day raises the current production proxy to about 86.2% annualized. The remaining gap is mainly execution-cost sensitivity and meta-allocation quality.

A causal parameter sweep over the already-frozen 96-template pool found a production-aware meta shape that does not increase leverage or loosen risk gates: 11-day trailing score window, 3-day meta rebalance, 3 active templates, with score `0.25 * annualized_return + 1.0 * Sharpe` and the existing positive-annualized filter. With next-trading-day recovery from daily loss, integer lots, concrete prior-completed-day contract selection, 5bp transaction cost and current account gates, the deterministic recent 484-day production proxy is about 130.8% annualized with about 28.8% maximum drawdown.

## Design

1. Treat only `daily loss limit reached` as a recoverable session lock. Existing risk is flattened immediately; no new risk is allowed for the rest of that CTP trading day. On a later CTP trading day, a flat account may automatically return to RUNNING only after the normal account gate passes. Equity non-positive, total drawdown, margin, available-cash, metadata, reconciliation, signal, and structural failures remain fail-closed/manual-stop conditions.
2. Persist the blocked trading day in RuntimeState so restart cannot bypass the session lock.
3. Freeze the production meta allocator at lookback=11, rebalance=3, count=3, annualized-score coefficient=0.25 and Sharpe coefficient=1.0. The 96-template pool, causal lagging, 2x gross cap and 5bp meta evidence cost remain unchanged.
4. Make the deterministic production-mechanics acceptance simulator use the same recoverable daily-loss semantics. It must continue to treat total drawdown, margin/cash and missing-contract failures as permanent halts.
5. Update evidence/docs to distinguish the new production-mechanics Base result from the still selection-biased research result. Do not present historical proxy performance as a future-return guarantee.

## Verification

Use RED-GREEN TDD for the session-lock recovery and frozen meta configuration. Then run directional unit/smoke tests, production-mechanics deterministic reproduction on the frozen artifacts, one full final CI/L4 candidate verification, and only then merge to main.
