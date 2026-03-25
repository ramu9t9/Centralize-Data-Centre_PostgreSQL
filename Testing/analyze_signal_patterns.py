"""
Pattern Analysis - What Makes Continuation Signals Work?

Analyzes the characteristics of winning vs losing signals to identify patterns:
1. Futures price movement correlation
2. Greeks behavior (delta, IV changes)
3. Volume patterns
4. Bid-ask spread
5. Time of day patterns
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
    candles['body_pct'] = (abs(candles['close'] - candles['open']) / candles['open']) * 100
    candles['range_pct'] = ((candles['high'] - candles['low']) / candles['open']) * 100
    return candles

def detect_signals(candles, lookforward=2):
    signals = []
    for i in range(len(candles) - lookforward):
        current_dir = candles.iloc[i]['direction']
        if current_dir == 0:
            continue
        next_dirs = [candles.iloc[i+j]['direction'] for j in range(1, lookforward+1)]
        if all(d == current_dir for d in next_dirs):
            # Get candle characteristics
            candle = candles.iloc[i]
            signals.append({
                'timestamp': candles.index[i],
                'direction': 'BULLISH' if current_dir == 1 else 'BEARISH',
                'entry_price': candle['close'],
                'body_pct': candle['body_pct'],
                'range_pct': candle['range_pct'],
                'volume': candle['volume'],
                'hour': candles.index[i].hour,
                'minute': candles.index[i].minute,
            })
    return pd.DataFrame(signals)

def get_futures_data(conn, timestamp, symbol='NIFTY24FEB26FUT'):
    """Get futures data at signal time"""
    ts_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
    
    query = f"""
        SELECT ltp, bid, ask, volume, oi, delta, gamma, theta, vega, iv
        FROM ltp_ticks
        WHERE symbol = '{symbol}'
          AND ts >= datetime('{ts_str}', '-30 seconds')
          AND ts <= datetime('{ts_str}', '+30 seconds')
        ORDER BY ABS(julianday(ts) - julianday('{ts_str}'))
        LIMIT 1
    """
    
    result = pd.read_sql_query(query, conn)
    return result.iloc[0] if len(result) > 0 else None

def get_atm_option_data(conn, timestamp, spot_price):
    """Get ATM option Greeks at signal time"""
    # Find closest strike (round to nearest 50)
    atm_strike = round(spot_price / 50) * 50
    
    ts_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
    
    # Try to find ATM CE and PE
    query = f"""
        SELECT symbol, ltp, delta, iv, bid, ask
        FROM ltp_ticks
        WHERE (symbol LIKE '%{int(atm_strike)}CE' OR symbol LIKE '%{int(atm_strike)}PE')
          AND ts >= datetime('{ts_str}', '-30 seconds')
          AND ts <= datetime('{ts_str}', '+30 seconds')
        ORDER BY ABS(julianday(ts) - julianday('{ts_str}'))
        LIMIT 2
    """
    
    result = pd.read_sql_query(query, conn)
    
    if len(result) > 0:
        ce_data = result[result['symbol'].str.contains('CE')]
        pe_data = result[result['symbol'].str.contains('PE')]
        
        return {
            'ce_iv': ce_data['iv'].iloc[0] if len(ce_data) > 0 else None,
            'pe_iv': pe_data['iv'].iloc[0] if len(pe_data) > 0 else None,
            'ce_delta': ce_data['delta'].iloc[0] if len(ce_data) > 0 else None,
            'pe_delta': pe_data['delta'].iloc[0] if len(pe_data) > 0 else None,
        }
    return None

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
            return {'exit_price': stop_loss, 'pnl_pct': pnl, 'win': False}
    
    exit_price = future_data.iloc[-1]['ltp']
    pnl = ((exit_price - entry_price) / entry_price) * 100 * direction
    return {'exit_price': exit_price, 'pnl_pct': pnl, 'win': pnl > 0}

print("=" * 80)
print("PATTERN ANALYSIS - What Makes Signals Win?")
print("=" * 80)

conn = db.get_connection()

# Load spot data
spot_df = pd.read_sql_query("""
    SELECT ts, ltp, volume FROM ltp_ticks
    WHERE symbol = 'NIFTY 50' AND ts::date = '2025-12-30'
    ORDER BY ts
""", conn)

print(f"\nAnalyzing: Dec 30, 2025 ({len(spot_df):,} ticks)")

# Build 20s candles (best performer)
candles = build_candles(spot_df.copy(), 20)
signals = detect_signals(candles, lookforward=2)

print(f"Signals detected: {len(signals)}")

# Enrich signals with futures and options data
print("\nEnriching signals with market data...")
enriched_signals = []

for idx, signal in signals.iterrows():
    # Get futures data
    futures = get_futures_data(conn, pd.to_datetime(signal['timestamp']))
    
    # Get ATM options data
    options = get_atm_option_data(conn, pd.to_datetime(signal['timestamp']), signal['entry_price'])
    
    # Backtest
    result = backtest_signal(signal, spot_df)
    
    if result and futures is not None:
        enriched = {
            **signal.to_dict(),
            'futures_ltp': futures['ltp'],
            'futures_delta': futures['delta'],
            'futures_iv': futures['iv'],
            'futures_bid': futures['bid'],
            'futures_ask': futures['ask'],
            'futures_spread_pct': ((futures['ask'] - futures['bid']) / futures['ltp'] * 100) if futures['bid'] > 0 else None,
            **result
        }
        
        if options:
            enriched.update({
                'ce_iv': options['ce_iv'],
                'pe_iv': options['pe_iv'],
                'iv_skew': (options['pe_iv'] - options['ce_iv']) if options['ce_iv'] and options['pe_iv'] else None,
            })
        
        enriched_signals.append(enriched)

df = pd.DataFrame(enriched_signals)
conn.close()

print(f"Enriched: {len(df)} signals with market data")

# Analyze patterns
print("\n" + "=" * 80)
print("PATTERN ANALYSIS - Winners vs Losers")
print("=" * 80)

winners = df[df['win'] == True]
losers = df[df['win'] == False]

print(f"\nWinners: {len(winners)} | Losers: {len(losers)}")

# Compare characteristics
print("\n1. CANDLE CHARACTERISTICS:")
print(f"   Body Size:")
print(f"      Winners: {winners['body_pct'].mean():.4f}% (avg)")
print(f"      Losers:  {losers['body_pct'].mean():.4f}% (avg)")

print(f"\n   Range Size:")
print(f"      Winners: {winners['range_pct'].mean():.4f}% (avg)")
print(f"      Losers:  {losers['range_pct'].mean():.4f}% (avg)")

print(f"\n   Volume:")
print(f"      Winners: {winners['volume'].mean():.0f} (avg)")
print(f"      Losers:  {losers['volume'].mean():.0f} (avg)")

print("\n2. FUTURES DATA:")
if 'futures_delta' in df.columns:
    print(f"   Delta:")
    print(f"      Winners: {winners['futures_delta'].mean():.4f} (avg)")
    print(f"      Losers:  {losers['futures_delta'].mean():.4f} (avg)")

if 'futures_iv' in df.columns:
    print(f"\n   IV:")
    print(f"      Winners: {winners['futures_iv'].mean():.2f}% (avg)")
    print(f"      Losers:  {losers['futures_iv'].mean():.2f}% (avg)")

if 'futures_spread_pct' in df.columns:
    print(f"\n   Spread:")
    print(f"      Winners: {winners['futures_spread_pct'].mean():.4f}% (avg)")
    print(f"      Losers:  {losers['futures_spread_pct'].mean():.4f}% (avg)")

print("\n3. TIME PATTERNS:")
print(f"   Hour Distribution (Winners):")
print(winners['hour'].value_counts().sort_index().head(10))

print("\n4. DIRECTION BREAKDOWN:")
for direction in ['BULLISH', 'BEARISH']:
    dir_df = df[df['direction'] == direction]
    dir_wins = dir_df[dir_df['win']].shape[0]
    dir_total = len(dir_df)
    print(f"\n   {direction}: {dir_wins}/{dir_total} ({dir_wins/dir_total*100:.1f}% WR)")

print("\n" + "=" * 80)
print("KEY INSIGHTS FOR STRATEGY FILTERS:")
print("=" * 80)
print("✅ Use these patterns to filter high-quality signals")
print("✅ Next: Apply filters and re-backtest on all days")
print("=" * 80)

# Save enriched data for further analysis
df.to_csv('signal_patterns.csv', index=False)
print("\n📊 Saved detailed analysis to: signal_patterns.csv")
