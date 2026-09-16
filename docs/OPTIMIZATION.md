# Parameter Optimization

## Running RSI Optimization

```bash
python analysis/optimize_rsi_params.py
```

Results save to: `results/current/rsi_optimization/`

## Interpreting Results

- **summary.csv** - Best parameters found
- **all_params.csv** - All tested combinations
- Look for: High Sharpe ratio, Win rate > 50%

## Running SuperTrend Optimization

```bash
python analysis/optimize_supertrend.py
```
