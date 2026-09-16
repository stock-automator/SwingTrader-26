# System Architecture

## Components

- **src/agent.py** - Main trading agent
- **src/strategies/** - Strategy implementations
- **src/data/** - Market data fetching
- **src/utils/** - Utilities (daily_trade_signals.py)
- **analysis/** - Optimization and analysis scripts

## Data Flow

1. `update_data.py` fetches market data
2. Strategies analyze data and generate signals
3. `agent.py` executes trades
4. Results saved to `results/`
