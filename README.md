# afuture

A futures arbitrage research and execution framework.

## Current scope

The first version focuses on:

- Chinese futures calendar spread statistical arbitrage
- Spread calculation and z-score signals
- Backtest framework with fees and slippage placeholders
- Risk controls for small accounts
- Simulation-first architecture

This project is designed for research and paper trading first. It is not a live trading system yet.

## Strategy

Initial strategy:

1. Select two contracts of the same commodity.
2. Build price spread.
3. Calculate rolling mean and standard deviation.
4. Open when spread deviates from historical range.
5. Close when spread returns toward equilibrium.

## Risk principles

- Never use full margin.
- Keep sufficient cash reserve.
- Limit single commodity exposure.
- Validate with realistic fees and slippage before live trading.

## Roadmap

- Add CTP market data adapter
- Add paper trading engine
- Add order management
- Add portfolio level risk control
- Add production reports
