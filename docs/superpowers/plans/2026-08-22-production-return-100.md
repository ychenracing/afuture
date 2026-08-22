# Production-Realizable 100% Return Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the historical production-mechanics Base acceptance path exceed 100% annualized without relaxing the existing risk gates or 2x target gross cap.

**Architecture:** Preserve the existing directional engine and 96-template strategy. Change daily-loss handling from permanent kill to a persisted same-trading-day session lock, then freeze the empirically selected causal meta allocator that closes the remaining production-cost gap. Keep all structural and capital-preservation failures fail-closed.

**Tech Stack:** Python 3.10/3.13, pandas/numpy, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-22-production-return-100-design.md`

## Global Constraints

- Target gross leverage remains <=2x.
- Daily loss remains 5%; total drawdown remains 30% in the directional live configuration.
- Margin and available-cash gates remain 35% and 25% respectively.
- Only daily-loss is automatically recoverable, and never before the next CTP trading day.
- Contract selection, integer lots, previous-completed activity, reduction-before-open and Broker truth remain production semantics.
- Validation is progressive: L1 targeted -> L2 directional smoke -> L3 frozen-artifact production benchmark -> one final L4/full CI.

---

### Task 1: Daily-loss session lock

**Files:** `afuture/state.py`, `afuture/directional_engine.py`, `tests/test_production_return_target.py`

- [ ] Write a failing test proving a daily-loss reduction remains locked on the same CTP day and resumes only on the next CTP trading day after flat/account-safe checks.
- [ ] Verify RED.
- [ ] Persist the blocked trading day and implement minimal automatic resume logic for daily-loss only.
- [ ] Run targeted directional-engine tests and verify GREEN.

### Task 2: Production-aware meta freeze

**Files:** `afuture/execution_aligned_policy.py`, `tests/test_production_return_target.py`, `tests/test_execution_aligned_policy.py`

- [ ] Write a failing test for lookback=11, rebalance=3, count=3, annualized coefficient=0.25 and Sharpe coefficient=1.0.
- [ ] Verify RED.
- [ ] Freeze the constants and use them in the trailing score function.
- [ ] Run targeted policy tests and verify GREEN.

### Task 3: Acceptance semantic parity

**Files:** `afuture/directional_acceptance.py`, `tools/test_directional_production_mechanics.py`, `tests/test_directional_acceptance_simulation.py`

- [ ] Add/adjust tests proving daily-loss flatten is session-recoverable while total-drawdown/margin/structural failures remain permanent.
- [ ] Verify RED where behavior changes.
- [ ] Update deterministic acceptance simulation to match production session-lock semantics.
- [ ] Run the directional production-mechanics smoke.

### Task 4: Evidence and final acceptance

**Files:** production evidence docs plus any evaluator metadata that describes the frozen meta score.

- [ ] Run the frozen-artifact recent-484-day Base 5bp production benchmark and require annualized >=100% without risk/leverage relaxation.
- [ ] Record Base/Stress metrics and limitations accurately.
- [ ] Run one final full CI/L4 candidate verification.
- [ ] Review diff for strategy/runtime/acceptance parity, commit, push and merge to `main`.
