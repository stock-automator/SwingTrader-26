import pandas as pd
import numpy as np
from pathlib import Path
from multiprocessing import Pool, cpu_count
from tqdm import tqdm
from itertools import product
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path("data/raw")
WATCHLIST_FILE = Path("watchlist.txt")
START_DATE = "2015-01-01"

# Parameter ranges to test
ATR_PERIODS = [7, 8, 9, 10, 11, 12, 14, 20]
ATR_MULTIPLIERS = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
EMA_PERIOD = 50

# ============================================================
# INDICATORS
# ============================================================

def calculate_atr(df, period=14):
    """Calculate Average True Range"""
    df = df.copy()
    df['high_low'] = df['High'] - df['Low']
    df['high_close'] = abs(df['High'] - df['Close'].shift())
    df['low_close'] = abs(df['Low'] - df['Close'].shift())
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=period).mean()
    return df['atr']


def calculate_supertrend(df, atr_period=10, multiplier=3.0):
    """Calculate SuperTrend indicator"""
    df = df.copy()
    
    df['atr'] = calculate_atr(df, atr_period)
    df['atr'] = df['atr'].bfill().fillna(df['atr'].mean())
    
    df['hl_avg'] = (df['High'] + df['Low']) / 2
    df['basic_ub'] = df['hl_avg'] + multiplier * df['atr']
    df['basic_lb'] = df['hl_avg'] - multiplier * df['atr']
    
    close = df['Close'].values
    basic_ub = df['basic_ub'].values
    basic_lb = df['basic_lb'].values
    
    final_ub = np.zeros(len(df))
    final_lb = np.zeros(len(df))
    
    final_ub[0] = basic_ub[0]
    final_lb[0] = basic_lb[0]
    
    for i in range(1, len(df)):
        if basic_ub[i] < final_ub[i-1] or close[i-1] > final_ub[i-1]:
            final_ub[i] = basic_ub[i]
        else:
            final_ub[i] = final_ub[i-1]
        
        if basic_lb[i] > final_lb[i-1] or close[i-1] < final_lb[i-1]:
            final_lb[i] = basic_lb[i]
        else:
            final_lb[i] = final_lb[i-1]
    
    df['final_ub'] = final_ub
    df['final_lb'] = final_lb
    
    trend = np.zeros(len(df))
    trend[0] = 1
    
    for i in range(1, len(df)):
        if trend[i-1] == 1:
            if close[i] <= final_lb[i]:
                trend[i] = -1
            else:
                trend[i] = 1
        else:
            if close[i] >= final_ub[i]:
                trend[i] = 1
            else:
                trend[i] = -1
    
    df['st_trend'] = trend
    
    signals = np.zeros(len(df))
    for i in range(1, len(df)):
        if trend[i] != trend[i-1]:
            signals[i] = 1 if trend[i] == 1 else -1
    
    df['st_signal'] = signals
    
    return df


def load_from_cache(ticker):
    """Load ticker data from local parquet cache"""
    parquet_path = DATA_DIR / f"{ticker}.parquet"
    
    if not parquet_path.exists():
        return None
    
    try:
        df = pd.read_parquet(parquet_path)
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        
        df = df[df.index >= START_DATE]
        return df
    except:
        return None


# ============================================================
# METRICS
# ============================================================

def calculate_metrics(trades, df_close):
    """Calculate performance metrics"""
    
    if len(trades) == 0:
        return None
    
    profits = np.array([t['profit'] for t in trades])
    
    if len(profits) == 0:
        return None
    
    total_profit = profits.sum()
    num_trades = len(profits)
    winning_trades = (profits > 0).sum()
    
    if num_trades == 0:
        return None
    
    win_rate = (winning_trades / num_trades * 100)
    
    avg_win = profits[profits > 0].mean() if winning_trades > 0 else 0
    avg_loss = abs(profits[profits < 0].mean()) if (profits < 0).sum() > 0 else 0
    
    profit_factor = total_profit / abs(profits[profits < 0].sum()) if (profits < 0).sum() > 0 else total_profit
    
    cumulative_profit = np.cumsum(profits)
    running_max = np.maximum.accumulate(cumulative_profit)
    drawdown = cumulative_profit - running_max
    max_drawdown = abs(drawdown.min()) if len(drawdown) > 0 else 0
    
    sharpe = 0
    if len(profits) > 1:
        returns = profits / 100  # Rough estimate
        if np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
    
    return {
        'total_profit': total_profit,
        'num_trades': num_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'sharpe_ratio': sharpe,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
    }


# ============================================================
# BACKTEST
# ============================================================

def backtest_with_params(ticker, df, atr_period, multiplier):
    """Backtest with specific parameters"""
    
    if df is None or len(df) < 100:
        return None
    
    df = calculate_supertrend(df, atr_period, multiplier)
    
    trades = []
    position = None
    entry_price = 0
    entry_date = None
    
    for i in range(EMA_PERIOD, len(df)):
        close = df['Close'].iloc[i]
        date = df.index[i]
        signal = df['st_signal'].iloc[i]
        
        if signal == 1 and position is None:
            entry_price = close
            entry_date = date
            position = 'long'
        
        elif signal == -1 and position == 'long':
            exit_price = close
            profit = exit_price - entry_price
            
            trades.append({
                'entry_price': entry_price,
                'exit_price': exit_price,
                'profit': profit,
                'days_held': (date - entry_date).days,
            })
            
            position = None
    
    if not trades:
        return None
    
    metrics = calculate_metrics(trades, df['Close'])
    
    if metrics:
        metrics['ticker'] = ticker
        metrics['atr_period'] = atr_period
        metrics['multiplier'] = multiplier
    
    return metrics


# ============================================================
# WORKER
# ============================================================

def test_params_on_ticker(args):
    """Test all parameter combinations on one ticker"""
    ticker, atr_period, multiplier = args
    
    df = load_from_cache(ticker)
    if df is None or len(df) < 100:
        return None
    
    return backtest_with_params(ticker, df, atr_period, multiplier)


# ============================================================
# MAIN
# ============================================================

def main():
    """Run parameter optimization"""
    
    if not WATCHLIST_FILE.exists():
        print(f"❌ {WATCHLIST_FILE} not found")
        return
    
    with open(WATCHLIST_FILE, 'r') as f:
        tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith('#')]
    
    print()
    print("=" * 100)
    print("🎯 SUPERTREND PARAMETER OPTIMIZATION")
    print("=" * 100)
    print(f"📈 Stocks: {len(tickers)}")
    print(f"🔍 ATR Periods: {ATR_PERIODS} ({len(ATR_PERIODS)} options)")
    print(f"📊 Multipliers: {ATR_MULTIPLIERS} ({len(ATR_MULTIPLIERS)} options)")
    print(f"⚙️  Total combinations: {len(ATR_PERIODS) * len(ATR_MULTIPLIERS)}")
    print(f"🔄 Total backtests: {len(tickers) * len(ATR_PERIODS) * len(ATR_MULTIPLIERS):,}")
    print(f"🖥️  Threads: {cpu_count()}")
    print("=" * 100)
    print()
    
    # Generate all parameter combinations
    param_combos = list(product(tickers, ATR_PERIODS, ATR_MULTIPLIERS))
    
    # Parallel processing
    with Pool(cpu_count()) as pool:
        all_results = list(tqdm(
            pool.imap_unordered(test_params_on_ticker, param_combos),
            total=len(param_combos),
            desc="Testing parameters",
            unit="test"
        ))
    
    # Filter results
    all_results = [r for r in all_results if r is not None]
    
    if not all_results:
        print("❌ No results")
        return
    
    results_df = pd.DataFrame(all_results)
    
    # Find best overall parameters
    print()
    print("=" * 100)
    print("🏆 BEST PARAMETERS (All Stocks Combined)")
    print("=" * 100)
    
    best_overall = results_df.nlargest(5, 'total_profit')[['atr_period', 'multiplier', 'total_profit', 'win_rate', 'sharpe_ratio', 'profit_factor']]
    
    print(f"{'Rank':<6} {'ATR':<6} {'Mult':<6} {'Profit£':>12} {'Win%':>8} {'Sharpe':>8} {'Profit Factor':>12}")
    print("-" * 100)
    
    for idx, (_, row) in enumerate(best_overall.iterrows(), 1):
        print(
            f"{idx:<6} "
            f"{int(row['atr_period']):<6} "
            f"{row['multiplier']:<6.1f} "
            f"£{row['total_profit']:>11.2f} "
            f"{row['win_rate']:>7.1f}% "
            f"{row['sharpe_ratio']:>8.2f} "
            f"{row['profit_factor']:>11.2f}"
        )
    
    # Best per stock
    print()
    print("=" * 100)
    print("🎯 BEST PARAMETERS PER STOCK")
    print("=" * 100)
    
    best_per_stock = results_df.loc[results_df.groupby('ticker')['total_profit'].idxmax()]
    best_per_stock = best_per_stock.nlargest(20, 'total_profit')[['ticker', 'atr_period', 'multiplier', 'total_profit', 'win_rate']]
    
    print(f"{'Ticker':<8} {'ATR':<6} {'Mult':<6} {'Profit£':>12} {'Win%':>8}")
    print("-" * 100)
    
    for _, row in best_per_stock.iterrows():
        print(
            f"{row['ticker']:<8} "
            f"{int(row['atr_period']):<6} "
            f"{row['multiplier']:<6.1f} "
            f"£{row['total_profit']:>11.2f} "
            f"{row['win_rate']:>7.1f}%"
        )
    
    # Comparison: default vs best
    print()
    print("=" * 100)
    print("📊 IMPROVEMENT: Default vs Optimized")
    print("=" * 100)
    
    default_params = results_df[(results_df['atr_period'] == 10) & (results_df['multiplier'] == 3.0)]
    best_params = results_df.nlargest(1, 'total_profit').iloc[0]
    
    default_profit = default_params['total_profit'].sum()
    best_profit = results_df['total_profit'].max() * len(tickers)  # Rough estimate
    
    improvement = best_profit - default_profit
    improvement_pct = (improvement / default_profit * 100) if default_profit > 0 else 0
    
    print(f"Default (ATR=10, Mult=3.0):     £{default_profit:>10,.2f}")
    print(f"Best found (ATR={int(best_params['atr_period'])}, Mult={best_params['multiplier']}): £{best_profit:>10,.2f}")
    print(f"Improvement:                   £{improvement:>10,.2f} ({improvement_pct:.1f}%)")
    
    # Save all results
    results_df.to_csv('optimization_results_all.csv', index=False)
    best_per_stock.to_csv('optimization_best_per_stock.csv', index=False)
    
    print()
    print("=" * 100)
    print("💾 Results saved:")
    print("   - optimization_results_all.csv (all parameter combinations)")
    print("   - optimization_best_per_stock.csv (best per stock)")
    print("=" * 100)
    print()


if __name__ == "__main__":
    main()
