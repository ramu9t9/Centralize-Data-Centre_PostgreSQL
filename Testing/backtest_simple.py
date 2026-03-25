"""
Simplified Scalping Backtest - Win Rate Calculation

Focuses on pure price action signals without futures correlation
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

def build_candles(df, timeframe_seconds):
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts')
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles['volume'] = df['volume'].resample(f'{timeframe_seconds}S').sum()
    candles = candles.dropna()
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
    candles['body_pct'] = (abs(candles['close'] - candles['open']) / candles['open']) * 100
    return candles

def detect_signals(candles, lookforward=2):
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
            })
    return pd.DataFrame(signals)

def backtest_signal(signal, spot_df):
    entry_price = signal['entry_price']
    entry_time = pd.to_datetime(signal['timestamp'])
    direction = 1 if signal['direction'] == 'BULLISH' else -1
    
    # Convert spot_df timestamps to datetime
    spot_df_copy = spot_df.copy()
    spot_df_copy['ts'] = pd.to_datetime(spot_df_copy['ts'])
    
    # Get next 60 seconds (2 candles)
    future_data = spot_df_copy[spot_df_copy['ts'] > entry_time].head(12)
    
    if len(future_data) == 0:
        return None
    
    # Stop loss: 0.1%
    stop_pct = 0.1
    stop_loss = entry_price * (1 - (stop_pct/100) * direction)
    
    # Track outcome
    for idx, row in future_data.iterrows():
        price = row['ltp']
        
        # Check stop
        if (direction == 1 and price <= stop_loss) or (direction == -1 and price >= stop_loss):
            pnl = ((stop_loss - entry_price) / entry_price) * 100 * direction
            return {'exit_price': stop_loss, 'pnl_pct': pnl, 'exit_reason': 'STOP', 'win': False}
    
    # Exit at end
    exit_price = future_data.iloc[-1]['ltp']
    pnl = ((exit_price - entry_price) / entry_price) * 100 * direction
    
    return {'exit_price': exit_price, 'pnl_pct': pnl, 'exit_reason': 'TIME', 'win': pnl > 0}

print("=" * 80)
print("SCALPING BACKTEST - Win Rate Analysis")
print("=" * 80)

conn = db.get_connection()

spot_df = pd.read_sql_query("""
    SELECT ts, ltp, volume FROM ltp_ticks
    WHERE symbol = 'NIFTY 50' AND ts::date = '2025-12-30'
    ORDER BY ts
""", conn)
conn.close()

print(f"\nData: {len(spot_df):,} ticks (Dec 30, 2025)")

# Test both timeframes
for tf in [20, 30]:
    print(f"\n{'=' * 80}")
    print(f"TIMEFRAME: {tf} SECONDS")
    print(f"{'=' * 80}")
    
    candles = build_candles(spot_df.copy(), tf)
    signals = detect_signals(candles, lookforward=2)
    
    print(f"\nSignals: {len(signals)}")
    
    # Backtest
    results = []
    for idx, signal in signals.iterrows():
        result = backtest_signal(signal, spot_df)
        if result:
            results.append({**signal.to_dict(), **result})
    
    if len(results) > 0:
        df = pd.DataFrame(results)
        
        wins = df['win'].sum()
        total = len(df)
        wr = (wins/total)*100
        
        avg_win = df[df['win']]['pnl_pct'].mean()
        avg_loss = df[~df['win']]['pnl_pct'].mean()
        avg_pnl = df['pnl_pct'].mean()
        
        expectancy = (wr/100 * avg_win) + ((1-wr/100) * avg_loss)
        
        print(f"\nRESULTS:")
        print(f"  Total Trades: {total}")
        print(f"  Wins: {wins} ({wr:.1f}%)")
        print(f"  Losses: {total-wins} ({100-wr:.1f}%)")
        print(f"  Avg Win: +{avg_win:.3f}%")
        print(f"  Avg Loss: {avg_loss:.3f}%")
        print(f"  Avg P&L: {avg_pnl:.3f}%")
        print(f"  Expectancy: {expectancy:.3f}%")
        
        # By direction
        for dir in ['BULLISH', 'BEARISH']:
            d = df[df['direction'] == dir]
            if len(d) > 0:
                print(f"\n  {dir}: {len(d)} trades, WR={d['win'].sum()/len(d)*100:.1f}%, Avg={d['pnl_pct'].mean():.3f}%")

print(f"\n{'=' * 80}")
print("COMPLETE")
print(f"{'=' * 80}")
