"""
Scalping Signal Backtester - Correlate with Futures Greeks & Calculate Win Rate

Takes continuation signals and:
1. Correlates with NIFTY futures data at signal time
2. Adds Greeks (delta, IV) as confirmation filters
3. Backtests each signal to calculate win rate
4. Calculates profit potential
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db
import pandas as pd
import numpy as np


def build_candles(df, timeframe_seconds):
    """Build OHLC candles from tick data"""
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts')
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles['volume'] = df['volume'].resample(f'{timeframe_seconds}S').sum()
    candles = candles.dropna()
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
    candles['body_size'] = abs(candles['close'] - candles['open'])
    candles['body_pct'] = (candles['body_size'] / candles['open']) * 100
    return candles

def detect_continuation_signals(candles, lookforward=2):
    """Detect continuation patterns"""
    signals = []
    for i in range(len(candles) - lookforward):
        current_dir = candles.iloc[i]['direction']
        if current_dir == 0:
            continue
        next_dirs = [candles.iloc[i+j]['direction'] for j in range(1, lookforward+1)]
        if all(d == current_dir for d in next_dirs):
            signals.append({
                'timestamp': candles.index[i],
                'direction': 'BULLISH' if current_dir == 1 else 'BEARISH',
                'entry_price': candles.iloc[i]['close'],
                'body_pct': candles.iloc[i]['body_pct'],
                'target_1': candles.iloc[i+1]['close'],
                'target_2': candles.iloc[i+2]['close'] if lookforward >= 2 else None,
            })
    return pd.DataFrame(signals)

def get_futures_data_at_time(conn, timestamp, window_seconds=30):
    """Get NIFTY futures data around signal time"""
    ts_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
    
    query = f"""
        SELECT ltp, bid, ask, volume, oi, delta, gamma, theta, vega, iv
        FROM ltp_ticks
        WHERE symbol LIKE '%FUT'
          AND ts >= datetime('{ts_str}', '-{window_seconds} seconds')
          AND ts <= datetime('{ts_str}', '+{window_seconds} seconds')
        ORDER BY ts
        LIMIT 1
    """
    
    result = pd.read_sql_query(query, conn)
    return result.iloc[0] if len(result) > 0 else None

def backtest_signal(signal, spot_df):
    """
    Backtest a single signal
    
    Entry: Signal candle close
    Target: Next 2 candles (scalp 1-2 candles)
    Stop: 0.1% from entry (tight scalp stop)
    """
    entry_price = signal['entry_price']
    entry_time = signal['timestamp']
    direction = 1 if signal['direction'] == 'BULLISH' else -1
    
    # Get next 60 seconds of data (2 candles @ 30s each)
    future_data = spot_df[spot_df['ts'] > entry_time].head(12)  # 12 ticks @ 5s = 60s
    
    if len(future_data) == 0:
        return None
    
    # Calculate stop loss (0.1% from entry)
    stop_loss = entry_price * (1 - 0.001 * direction)
    
    # Track P&L
    max_profit = 0
    max_loss = 0
    exit_price = None
    exit_reason = None
    
    for idx, row in future_data.iterrows():
        price = row['ltp']
        pnl_pct = ((price - entry_price) / entry_price) * 100 * direction
        
        # Check stop loss
        if (direction == 1 and price <= stop_loss) or (direction == -1 and price >= stop_loss):
            exit_price = stop_loss
            exit_reason = 'STOP_LOSS'
            break
        
        # Track max profit/loss
        max_profit = max(max_profit, pnl_pct)
        max_loss = min(max_loss, pnl_pct)
    
    # If not stopped out, exit at end of 2 candles
    if exit_price is None:
        exit_price = future_data.iloc[-1]['ltp']
        exit_reason = 'TARGET_TIME'
    
    final_pnl = ((exit_price - entry_price) / entry_price) * 100 * direction
    
    return {
        'exit_price': exit_price,
        'exit_reason': exit_reason,
        'pnl_pct': final_pnl,
        'max_profit_pct': max_profit,
        'max_loss_pct': max_loss,
        'win': final_pnl > 0
    }

# Main analysis
print("=" * 80)
print("SCALPING BACKTEST - Futures Greeks Correlation & Win Rate")
print("=" * 80)

conn = sqlite3.connect(str(db_path))

# Load spot data
print("\n1. Loading NIFTY 50 spot data (Dec 30, 2025)...")
spot_query = """
    SELECT ts, ltp, volume
    FROM ltp_ticks
    WHERE symbol = 'NIFTY 50'
      AND ts::date = '2025-12-30'
    ORDER BY ts
"""
spot_df = pd.read_sql_query(spot_query, conn)
print(f"   Loaded: {len(spot_df):,} ticks")

# Build 30s candles and detect signals
print("\n2. Detecting 30s continuation signals...")
candles = build_candles(spot_df.copy(), 30)
signals = detect_continuation_signals(candles, lookforward=2)
print(f"   Found: {len(signals)} signals")

# Correlate with futures data
print("\n3. Correlating with NIFTY futures Greeks...")
signals_with_futures = []

for idx, signal in signals.iterrows():
    futures_data = get_futures_data_at_time(conn, signal['timestamp'])
    
    if futures_data is not None:
        signal_dict = signal.to_dict()
        signal_dict['futures_ltp'] = futures_data['ltp']
        signal_dict['futures_delta'] = futures_data['delta']
        signal_dict['futures_iv'] = futures_data['iv']
        signal_dict['futures_bid'] = futures_data['bid']
        signal_dict['futures_ask'] = futures_data['ask']
        signals_with_futures.append(signal_dict)

signals_df = pd.DataFrame(signals_with_futures)
print(f"   Matched: {len(signals_df)} signals with futures data")

# Backtest each signal
print("\n4. Backtesting signals...")
backtest_results = []

for idx, signal in signals_df.iterrows():
    result = backtest_signal(signal, spot_df)
    if result:
        backtest_results.append({**signal.to_dict(), **result})

results_df = pd.DataFrame(backtest_results)

# Calculate statistics
print("\n" + "=" * 80)
print("BACKTEST RESULTS")
print("=" * 80)

if len(results_df) > 0:
    total_trades = len(results_df)
    wins = results_df['win'].sum()
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100
    
    avg_win = results_df[results_df['win']]['pnl_pct'].mean()
    avg_loss = results_df[~results_df['win']]['pnl_pct'].mean()
    avg_pnl = results_df['pnl_pct'].mean()
    
    print(f"\nTotal Signals: {total_trades}")
    print(f"Wins: {wins} ({win_rate:.1f}%)")
    print(f"Losses: {losses} ({100-win_rate:.1f}%)")
    print(f"\nAverage Win: {avg_win:.3f}%")
    print(f"Average Loss: {avg_loss:.3f}%")
    print(f"Average P&L: {avg_pnl:.3f}%")
    print(f"\nExpectancy: {(win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss):.3f}%")
    
    # Breakdown by direction
    print(f"\n{'=' * 80}")
    print("BY DIRECTION")
    print(f"{'=' * 80}")
    
    for direction in ['BULLISH', 'BEARISH']:
        dir_df = results_df[results_df['direction'] == direction]
        if len(dir_df) > 0:
            dir_wins = dir_df['win'].sum()
            dir_wr = (dir_wins / len(dir_df)) * 100
            dir_avg_pnl = dir_df['pnl_pct'].mean()
            print(f"\n{direction}:")
            print(f"  Trades: {len(dir_df)}")
            print(f"  Win Rate: {dir_wr:.1f}%")
            print(f"  Avg P&L: {dir_avg_pnl:.3f}%")
    
    # Sample trades
    print(f"\n{'=' * 80}")
    print("SAMPLE TRADES (First 10)")
    print(f"{'=' * 80}")
    print(results_df[['timestamp', 'direction', 'entry_price', 'exit_price', 'pnl_pct', 'exit_reason']].head(10).to_string(index=False))

conn.close()

print(f"\n{'=' * 80}")
print("ANALYSIS COMPLETE")
print(f"{'=' * 80}")
