"""
Simplified Pattern Analysis - Focus on Spot Price Action Patterns

Analyzes what makes continuation signals work based on:
1. Candle characteristics (body size, range, volume)
2. Time of day patterns
3. Price momentum
4. Consecutive candle behavior
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db
import pandas as pd
import numpy as np


def build_candles(df, timeframe_seconds):
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts')
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles['volume'] = df['volume'].resample(f'{timeframe_seconds}S').sum()
    candles = candles.dropna()
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
    candles['body_size'] = abs(candles['close'] - candles['open'])
    candles['body_pct'] = (candles['body_size'] / candles['open']) * 100
    candles['range_pct'] = ((candles['high'] - candles['low']) / candles['open']) * 100
    candles['upper_wick'] = candles['high'] - candles[['open', 'close']].max(axis=1)
    candles['lower_wick'] = candles[['open', 'close']].min(axis=1) - candles['low']
    candles['wick_ratio'] = (candles['upper_wick'] + candles['lower_wick']) / candles['body_size']
    return candles

def detect_signals_with_context(candles, lookforward=2):
    """Detect signals and capture surrounding context"""
    signals = []
    
    for i in range(1, len(candles) - lookforward):  # Start from 1 to get previous candle
        current_dir = candles.iloc[i]['direction']
        if current_dir == 0:
            continue
            
        next_dirs = [candles.iloc[i+j]['direction'] for j in range(1, lookforward+1)]
        if all(d == current_dir for d in next_dirs):
            current = candles.iloc[i]
            prev = candles.iloc[i-1]
            
            signals.append({
                'timestamp': candles.index[i],
                'direction': 'BULLISH' if current_dir == 1 else 'BEARISH',
                'entry_price': current['close'],
                
                # Current candle characteristics
                'body_pct': current['body_pct'],
                'range_pct': current['range_pct'],
                'volume': current['volume'],
                'wick_ratio': current['wick_ratio'],
                
                # Previous candle (momentum check)
                'prev_direction': prev['direction'],
                'prev_body_pct': prev['body_pct'],
                'momentum': 1 if prev['direction'] == current_dir else 0,  # Same direction = momentum
                
                # Time context
                'hour': candles.index[i].hour,
                'minute': candles.index[i].minute,
                
                # Targets
                'target_1': candles.iloc[i+1]['close'],
                'target_2': candles.iloc[i+2]['close'],
            })
    
    return pd.DataFrame(signals)

def backtest_signal(signal, spot_df):
    entry_price = signal['entry_price']
    entry_time = pd.to_datetime(signal['timestamp'])
    direction = 1 if signal['direction'] == 'BULLISH' else -1
    
    spot_df_copy = spot_df.copy()
    spot_df_copy['ts'] = pd.to_datetime(spot_df_copy['ts'])
    future_data = spot_df_copy[spot_df_copy['ts'] > entry_time].head(12)
    
    if len(future_data) == 0:
        return None
    
    stop_pct = 0.1
    stop_loss = entry_price * (1 - (stop_pct/100) * direction)
    
    for idx, row in future_data.iterrows():
        price = row['ltp']
        if (direction == 1 and price <= stop_loss) or (direction == -1 and price >= stop_loss):
            pnl = ((stop_loss - entry_price) / entry_price) * 100 * direction
            return {'pnl_pct': pnl, 'win': False, 'exit_reason': 'STOP'}
    
    exit_price = future_data.iloc[-1]['ltp']
    pnl = ((exit_price - entry_price) / entry_price) * 100 * direction
    return {'pnl_pct': pnl, 'win': pnl > 0, 'exit_reason': 'TIME'}

print("=" * 80)
print("PATTERN ANALYSIS - What Makes Continuation Signals Win?")
print("=" * 80)

conn = db.get_connection()

spot_df = pd.read_sql_query("""
    SELECT ts, ltp, volume FROM ltp_ticks
    WHERE symbol = 'NIFTY 50' AND ts::date = '2025-12-30'
    ORDER BY ts
""", conn)
conn.close()

print(f"\nData: Dec 30, 2025 ({len(spot_df):,} ticks)")

# Build 20s candles
candles = build_candles(spot_df.copy(), 20)
signals = detect_signals_with_context(candles, lookforward=2)

print(f"Signals: {len(signals)}")

# Backtest all signals
print("\nBacktesting...")
results = []
for idx, signal in signals.iterrows():
    result = backtest_signal(signal, spot_df)
    if result:
        results.append({**signal.to_dict(), **result})

df = pd.DataFrame(results)

# Analysis
print("\n" + "=" * 80)
print("PATTERN ANALYSIS - Winners vs Losers")
print("=" * 80)

winners = df[df['win'] == True]
losers = df[df['win'] == False]

print(f"\nTotal: {len(df)} | Winners: {len(winners)} ({len(winners)/len(df)*100:.1f}%) | Losers: {len(losers)}")

print("\n1. CANDLE BODY SIZE:")
print(f"   Winners: {winners['body_pct'].mean():.4f}% (avg) | Median: {winners['body_pct'].median():.4f}%")
print(f"   Losers:  {losers['body_pct'].mean():.4f}% (avg) | Median: {losers['body_pct'].median():.4f}%")
print(f"   → Insight: {'Bigger bodies win more' if winners['body_pct'].mean() > losers['body_pct'].mean() else 'Smaller bodies win more'}")

print("\n2. CANDLE RANGE:")
print(f"   Winners: {winners['range_pct'].mean():.4f}% (avg)")
print(f"   Losers:  {losers['range_pct'].mean():.4f}% (avg)")

print("\n3. WICK RATIO (Wicks/Body):")
print(f"   Winners: {winners['wick_ratio'].mean():.2f} (avg)")
print(f"   Losers:  {losers['wick_ratio'].mean():.2f} (avg)")
print(f"   → Insight: {'Less wicks = cleaner signals' if winners['wick_ratio'].mean() < losers['wick_ratio'].mean() else 'More wicks acceptable'}")

print("\n4. MOMENTUM (Previous candle same direction):")
momentum_winners = winners[winners['momentum'] == 1]
no_momentum_winners = winners[winners['momentum'] == 0]
print(f"   With Momentum: {len(momentum_winners)}/{len(winners[winners['momentum'] == 1])+len(losers[losers['momentum'] == 1])} win")
print(f"   Without Momentum: {len(no_momentum_winners)}/{len(winners[winners['momentum'] == 0])+len(losers[losers['momentum'] == 0])} win")

print("\n5. TIME OF DAY:")
print("   Winners by Hour:")
for hour in sorted(winners['hour'].unique()):
    hour_winners = winners[winners['hour'] == hour]
    hour_total = df[df['hour'] == hour]
    wr = len(hour_winners) / len(hour_total) * 100
    print(f"      {hour:02d}:00 - {len(hour_winners)}/{len(hour_total)} ({wr:.1f}% WR)")

print("\n6. DIRECTION BREAKDOWN:")
for direction in ['BULLISH', 'BEARISH']:
    dir_winners = winners[winners['direction'] == direction]
    dir_total = df[df['direction'] == direction]
    print(f"   {direction}: {len(dir_winners)}/{len(dir_total)} ({len(dir_winners)/len(dir_total)*100:.1f}% WR)")

print("\n" + "=" * 80)
print("RECOMMENDED FILTERS:")
print("=" * 80)

# Calculate optimal thresholds
body_threshold = winners['body_pct'].quantile(0.25)  # Bottom 25% of winners
wick_threshold = winners['wick_ratio'].quantile(0.75)  # Top 25% of winners

print(f"1. Body Size > {body_threshold:.4f}% (filters weak signals)")
print(f"2. Wick Ratio < {wick_threshold:.2f} (filters indecisive candles)")
print(f"3. Momentum = 1 (previous candle same direction)")
print(f"4. Avoid hours with low win rate")

# Test filters
filtered = df[
    (df['body_pct'] > body_threshold) &
    (df['wick_ratio'] < wick_threshold) &
    (df['momentum'] == 1)
]

if len(filtered) > 0:
    filtered_wr = filtered['win'].sum() / len(filtered) * 100
    print(f"\nFiltered Results: {len(filtered)} signals, {filtered_wr:.1f}% WR")
    print(f"Improvement: {filtered_wr - (len(winners)/len(df)*100):.1f}% points")

print("\n" + "=" * 80)

# Save for analysis
df.to_csv('signal_patterns_detailed.csv', index=False)
print("📊 Saved: signal_patterns_detailed.csv")
