"""
Scalping Signal Generator - 20s/30s Candle Continuation Patterns

Analyzes NIFTY 50 spot data to find candles where next 1-2 candles move in same direction.
This identifies high-probability scalping setups.
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
    
    # Resample to specified timeframe
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles['volume'] = df['volume'].resample(f'{timeframe_seconds}S').sum()
    candles = candles.dropna()
    
    # Calculate candle direction
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,  # Bullish
                           np.where(candles['close'] < candles['open'], -1,  # Bearish
                                    0))  # Doji
    
    # Calculate candle body size
    candles['body_size'] = abs(candles['close'] - candles['open'])
    candles['body_pct'] = (candles['body_size'] / candles['open']) * 100
    
    return candles

def detect_continuation_signals(candles, lookforward=2):
    """
    Detect candles where next 1-2 candles move in same direction
    
    Args:
        candles: DataFrame with OHLC and direction
        lookforward: Number of candles to check (1 or 2)
    
    Returns:
        DataFrame with signals
    """
    signals = []
    
    for i in range(len(candles) - lookforward):
        current_dir = candles.iloc[i]['direction']
        
        if current_dir == 0:  # Skip doji candles
            continue
        
        # Check if next candles move in same direction
        next_dirs = [candles.iloc[i+j]['direction'] for j in range(1, lookforward+1)]
        
        # Signal if all next candles match current direction
        if all(d == current_dir for d in next_dirs):
            signals.append({
                'timestamp': candles.index[i],
                'direction': 'BULLISH' if current_dir == 1 else 'BEARISH',
                'open': candles.iloc[i]['open'],
                'close': candles.iloc[i]['close'],
                'body_pct': candles.iloc[i]['body_pct'],
                'next_1_close': candles.iloc[i+1]['close'],
                'next_2_close': candles.iloc[i+2]['close'] if lookforward >= 2 else None,
                'continuation_candles': lookforward
            })
    
    return pd.DataFrame(signals)

# Main analysis
print("=" * 80)
print("SCALPING SIGNAL DETECTION - CANDLE CONTINUATION PATTERNS")
print("=" * 80)

# Load NIFTY 50 spot data
print("\n1. Loading NIFTY 50 spot data...")
conn = db.get_connection()

# Get one day of data for testing (faster)
query = """
    SELECT ts, ltp, volume
    FROM ltp_ticks
    WHERE symbol = 'NIFTY 50'
      AND ts::date = '2025-12-30'
    ORDER BY ts
"""

df = pd.read_sql_query(query, conn)
conn.close()

print(f"   Loaded: {len(df):,} ticks for 2025-12-30")
print(f"   Time range: {df['ts'].min()} to {df['ts'].max()}")

# Analyze different timeframes
timeframes = [20, 30]  # 20s and 30s candles

for tf in timeframes:
    print(f"\n{'=' * 80}")
    print(f"TIMEFRAME: {tf} SECONDS")
    print(f"{'=' * 80}")
    
    # Build candles
    candles = build_candles(df.copy(), tf)
    print(f"\n   Total candles: {len(candles)}")
    print(f"   Bullish: {(candles['direction'] == 1).sum()}")
    print(f"   Bearish: {(candles['direction'] == -1).sum()}")
    print(f"   Doji: {(candles['direction'] == 0).sum()}")
    
    # Detect 1-candle continuation
    signals_1 = detect_continuation_signals(candles, lookforward=1)
    print(f"\n   1-CANDLE CONTINUATION SIGNALS: {len(signals_1)}")
    if len(signals_1) > 0:
        print(f"      Bullish: {(signals_1['direction'] == 'BULLISH').sum()}")
        print(f"      Bearish: {(signals_1['direction'] == 'BEARISH').sum()}")
        print(f"      Signal rate: {len(signals_1)/len(candles)*100:.1f}% of candles")
    
    # Detect 2-candle continuation
    signals_2 = detect_continuation_signals(candles, lookforward=2)
    print(f"\n   2-CANDLE CONTINUATION SIGNALS: {len(signals_2)}")
    if len(signals_2) > 0:
        print(f"      Bullish: {(signals_2['direction'] == 'BULLISH').sum()}")
        print(f"      Bearish: {(signals_2['direction'] == 'BEARISH').sum()}")
        print(f"      Signal rate: {len(signals_2)/len(candles)*100:.1f}% of candles")
    
    # Show sample signals
    if len(signals_2) > 0:
        print(f"\n   SAMPLE SIGNALS (first 5):")
        print(signals_2.head().to_string(index=False))

print(f"\n{'=' * 80}")
print("SUMMARY")
print(f"{'=' * 80}")
print("✅ Signal detection complete")
print("✅ Next: Correlate signals with futures Greeks and IV")
print("✅ Next: Calculate win rate and profit potential")
print(f"{'=' * 80}")
