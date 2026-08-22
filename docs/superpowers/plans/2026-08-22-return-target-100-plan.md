# afuture 100% Annualized Return Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate a low-gross-leverage multi-alpha futures portfolio that prioritizes reaching >=100% annualized historical return, then promote only candidates that survive causal and roll-safe checks into afuture's existing risk/execution chain.

**Architecture:** Keep research and production permissions separate. A new research evaluator creates same-exchange long/short pair returns from multiple causal alpha families, then a rolling alpha allocator chooses the strongest families using only past returns. Only a candidate that clears the return target and roll-safe specific-contract validation may be represented by a lightweight `AggressivePortfolioPolicy`; orders still flow through existing `RiskManager` and `PairExecutor`.

**Tech Stack:** Python 3.10/3.13, pandas, numpy, AKShare/Sina historical futures data, GitHub Actions, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-return-target-100-design.md`

## Global Constraints

- Historical signals must be causal: close-t information may only earn t→t+1 return.
- Continuous-contract data is L3 discovery evidence only; production promotion requires specific-contract roll-safe L4.
- Research gross leverage is capped at 2.0 by default; optional sensitivity may inspect 2.5, but target acceptance at this stage requires <=2.0.
- Base one-way turnover cost is 5bp, stress cost 15bp, extreme cost 30bp.
- Final OOS is non-pristine and must be labeled as such.
- No research component can create `OrderRequest`, mutate account state, or bypass `RiskManager`/`PairExecutor`.
- Verification follows `AGENTS.md`: L1 targeted tests → L2 smoke → L3 broad real data → L4 only for survivors → one final CI.

---

### Task 1: High-return multi-alpha research engine

**Files:**
- Create: `tools/evaluate_return_target_portfolio.py`
- Create: `tools/test_return_target_portfolio.py`

**Interfaces:**
- Consumes: `runtime/broad_daily_universe.csv` with columns `date`, `product`, `close` and optional `exchange` metadata inferred from a fixed product→exchange map.
- Produces: `evaluate(raw: pd.DataFrame) -> dict`, `build_family_returns(raw: pd.DataFrame) -> dict[str, pd.Series]`, `allocate_families(family_returns: dict[str, pd.Series], lookback: int, top_n: int, gross_leverage: float, cost_bps: float) -> pd.Series`.

- [ ] **Step 1: Write causal and risk-cap tests**

Create synthetic tests proving: family signals are shifted one day; same-exchange long/short pair construction does not reuse a product twice; turnover cost lowers PnL; `gross_leverage` never exceeds 2.0 in accepted configs; alpha allocation uses only returns strictly before the allocation date; target fields distinguish selected/in-sample from evaluation metrics.

- [ ] **Step 2: Run the targeted test and verify failure**

Run: `python tools/test_return_target_portfolio.py`
Expected: FAIL before the evaluator exists or before all invariants are implemented.

- [ ] **Step 3: Implement finite but aggressive family templates**

Implement causal templates for:
- cross-sectional momentum: lookback 5/10/20/40/60/120;
- reversal: 1/3/5/10;
- slow-fast: slow 20/40/60/120 with fast 3/5/10 and blend strengths 0.5/1.0;
- acceleration: medium return minus long return component;
- breakout/range-position: 20/40/60;
- volatility-adjusted versions using 10/20/40-day volatility;
- economic residual family reused from `evaluate_broad_pair_regime.py` as a separate return source.

Each cross-sectional family forms up to 1/2/3/4 disjoint same-exchange strongest-vs-weakest pairs. Pair weights are inverse-volatility normalized and market-neutral at family level.

- [ ] **Step 4: Implement rolling alpha allocation and target-oriented selection**

For allocator lookbacks 20/40/60/120 and top_n 1/2/3/4, rank families using lagged rolling annualized return, Sharpe, drawdown and recent hit ratio. Weak/negative families may receive zero weight. Evaluate gross leverage 1.0/1.25/1.5/1.75/2.0. Selection score weights annualized return most heavily while penalizing drawdown >30%, excessive turnover, and a single-window/family concentration.

Expose both:
- `selection_metrics`: Train+Validation/full-recent search metrics used for model selection;
- `evaluation_metrics`: prior windows and Final OOS, explicitly with `pristine_final_oos=false`.

- [ ] **Step 5: Run targeted L1/L2 tests**

Run: `python tools/test_return_target_portfolio.py`
Expected: PASS.

- [ ] **Step 6: Commit checkpoint**

Commit message: `feat: add return-target multi-alpha research engine`.

### Task 2: Real-data L3 search workflow

**Files:**
- Create: `.github/workflows/research-return-target.yml`
- Modify: `tools/fetch_broad_daily_universe.py` only if exchange metadata or coverage improvements are needed.

**Interfaces:**
- Consumes: Task 1 evaluator and existing broad data fetcher.
- Produces: artifact `return-target-broad-evidence` containing broad CSV, summary/failures and `runtime/return_target_portfolio_report.json`.

- [ ] **Step 1: Add isolated PR-triggered workflow**

Workflow paths are limited to the new evaluator/test/fetcher/workflow. Run targeted test first, fetch broad data once, evaluate all preregistered templates, and upload artifacts even when the economic target is missed.

- [ ] **Step 2: Open a draft PR against `main`**

Use branch `codex/afuture-return-100-target-20260822` so GitHub Actions supplies the real-data execution environment.

- [ ] **Step 3: Inspect L3 artifact rather than workflow color alone**

Record selected configuration, base/stress/extreme annualized return, max drawdown, active days, turnover, family contribution, prior-window behavior and Final OOS. Treat program success and target success as different fields.

- [ ] **Step 4: If target is below 100%, iterate on high-information alpha structure only**

Allowed second-wave additions, chosen from evidence rather than blind parameter expansion:
- regime-conditioned trend/reversal switching based on cross-sectional dispersion and market breadth;
- pair-level volatility targeting with leverage still capped at 2.0;
- alpha stacking (trend + reversal + residual) where each component has positive marginal contribution;
- monthly/quarterly seasonality using only prior-year observations;
- product-cluster neutralization and within-cluster relative strength;
- adaptive holding/rebalance horizon selected from lagged family quality.

Do not add a parameter if its only purpose is to exploit a single observed OOS segment.

- [ ] **Step 5: Repeat targeted L3 only after material research logic changes**

Stop L3 iteration when either base-cost annualized >=100% with drawdown <30% and stress positive, or the allowed family space cannot materially close the gap without leakage/high leverage.

### Task 3: Specific-contract L4 for any target-reaching candidate

**Files:**
- Create or modify a focused specific-contract fetch/evaluator pair only for the selected products/relations.
- Create targeted causal/roll tests.
- Create `.github/workflows/research-return-target-specific.yml`.

**Interfaces:**
- Consumes: exact selected L3 family/template and product relations.
- Produces: roll-safe concrete-contract PnL with `t` contract selection and same-contract `t→t+1` return.

- [ ] **Step 1: Freeze the selected L3 configuration before L4 data is read**

Persist exact family IDs, allocator lookback/top_n, pair count, rebalance horizon and leverage in the report/workflow inputs.

- [ ] **Step 2: Write roll-causality tests first**

Prove contract rolls cannot create synthetic return, delivery blackout applies before selection, and missing next-contract observations fail closed.

- [ ] **Step 3: Run targeted L4**

Acceptance requires base annualized >=100%, max drawdown >-30%, stress annualized >0, gross leverage <=2.0, and enough active days/trades to avoid a tiny-sample target.

- [ ] **Step 4: If L4 destroys the target, return to Task 2 with a documented rejection**

No production promotion occurs until specific contracts support the target.

### Task 4: Production policy integration after L4 pass

**Files:**
- Create: `afuture/aggressive_portfolio.py`
- Modify: `afuture/auto.py`
- Modify: `afuture/engine.py` only at the existing candidate-open permission boundary.
- Modify: `afuture/config.py`
- Add: focused tests under `tests/`.

**Interfaces:**
- Produces: `AggressivePortfolioPolicy.allowed_pair_ids(...) -> set[str]` and `risk_multiplier(pair_id: str) -> float`.
- Consumes only already-registered `PairConfig` candidates and lagged evidence. It cannot submit orders.

- [ ] **Step 1: Write permission-boundary tests**

Verify disabled policy is economic no-op; allowed IDs can only reduce the Auto open-eligible set; risk multiplier cannot exceed configured cap; protected/managed existing positions retain exit management; no direct broker/executor dependency exists.

- [ ] **Step 2: Implement minimal policy and config**

Keep policy state small and serializable. Any missing/stale evidence returns no new-risk permission (fail closed).

- [ ] **Step 3: Integrate at Auto candidate permission boundary**

Do not duplicate strategy or execution logic. Existing `RiskManager` and `PairExecutor` remain final authorities.

- [ ] **Step 4: Run affected Auto/engine tests and smoke replay**

Use targeted tests first, then the relevant Auto replay/acceptance subset.

### Task 5: Review, documentation, and final integration

**Files:**
- Modify: `README.md`
- Modify: `docs/data-and-backtest.md`
- Create/update: `docs/return-target-100-evidence.md`
- Modify other docs only where facts changed.

- [ ] **Step 1: Code-review pass 1**

Review the complete branch for look-ahead, roll artifacts, cost accounting, leverage/gross bugs, state/order permission bypass, and result overstatement. Fix every Critical/Important finding.

- [ ] **Step 2: Code-review pass 2 after fixes**

Repeat whole-diff review for regressions introduced by pass 1. Stop only when no behavior-affecting issue remains.

- [ ] **Step 3: Align code/comments/docs**

State exactly which result is selected/in-sample, which is non-pristine OOS, which is roll-safe, what leverage/cost was used, and whether the 100% target is actually met.

- [ ] **Step 4: Run one final complete CI**

Require Python 3.10 and 3.13 main CI success. Do not repeat expensive L3/L4 if final changes are documentation-only.

- [ ] **Step 5: Merge to `main`**

Squash merge the final PR only after the target result and review/CI evidence are recorded. Verify remote `main` points to the merged tree.
