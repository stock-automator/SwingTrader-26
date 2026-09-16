# Setup Guide

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

1. **Watchlist** - Edit `config/watchlist.txt` with symbols to trade
2. **Strategy Parameters** - Edit `config/trading_config.json`

## Running Signals

```bash
python src/utils/daily_trade_signals.py
```

## Testing

```bash
pytest tests/
```
