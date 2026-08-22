# Production Return 100% Implementation Plan

**Goal:** Make the directional production-account/mechanics path achieve >=100% annualized Base return without increasing the 2x gross leverage cap, while preserving hard drawdown/margin/cash gates.

**Architecture:** Replace lifetime shutdown after a daily-loss breach with a recoverable daily circuit breaker: flatten, block new risk for the rest of that CTP trading day, and automatically restore RUNNING on the next trading day only after broker readiness, metadata, position reconciliation, and account risk checks pass. Add a causal directional gross governor based only on completed realized account returns: with a two-day sample volatility >=3% or the most recent completed return <=-3%, scale directional target weights to 25%; otherwise use 100%. Tighten directional per-contract max volume from 100 to 35. Total drawdown, margin, cash reserve, metadata, reconciliation, and 2x gross leverage remain hard gates.

**Verification:** TDD for recovery state and causal gross scaling; L2 directional engine/runtime/risk tests; L3 independent production-mechanics windows and Base/Stress report; one final L4 CI/acceptance run after the candidate is stable. Acceptance requires full_recent production-mechanics Base annualized return >=100%, max drawdown <=30%, gross leverage <=2x, and no lookahead in governor inputs.
