"""
Scalping Signal Detection - NIFTY Spot Candle Analysis

Strategy:
1. Build 20s and 30s candles from 5s tick data
2. Identify candles where next 1-2 candles move in same direction
3. Count signal frequency
4. Later: Correlate with futures, Greeks, IV for pattern identification
"""

import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()

print("=" * 80)
print("SCALPING SIGNAL DETECTION - NIFTY SPOT ANALYSIS")
print("=" * 80)

# Step 1: Get NIFTY spot data (not futures, not options)
print("\n1. Loading NIFTY Spot data...")
query = """
    SELECT ts, ltp, volume
    FROM ltp_ticks
    WHERE symbol = 'NIFTY 50'
       OR symbol = 'NIFTY'
       OR symbol LIKE 'NIFTY%' AND symbol NOT LIKE '%FUT%' AND symbol NOT LIKE '%CE' AND symbol NOT LIKE '%PE'
    ORDER BY ts
    LIMIT 10
"""
cursor = conn.cursor()
cursor.execute(query)
sample = cursor.fetchall()

print(f"\nSample spot symbols found:")
for row in sample:
    print(f"  {row}")

# Get distinct spot symbols
cursor.execute("""
    SELECT DISTINCT symbol 
    FROM ltp_ticks 
    WHERE (symbol LIKE 'NIFTY%' OR symbol LIKE 'BANKNIFTY%')
      AND symbol NOT LIKE '%FUT%' 
      AND symbol NOT LIKE '%CE' 
      AND symbol NOT LIKE '%PE'
    LIMIT 20
""")
spot_symbols = cursor.fetchall()

print(f"\n2. Available Spot Symbols:")
if spot_symbols:
    for sym in spot_symbols:
        print(f"   ✅ {sym[0]}")
else:
    print("   ❌ NO SPOT SYMBOLS FOUND")
    print("\n   Checking for NIFTY futures instead...")
    cursor.execute("""
        SELECT DISTINCT symbol 
        FROM ltp_ticks 
        WHERE symbol LIKE '%FUT%'
        LIMIT 5
    """)
    futures = cursor.fetchall()
    if futures:
        print(f"\n   Available Futures (can use as proxy for spot):")
        for fut in futures:
            print(f"   ✅ {fut[0]}")

# Step 2: Determine which symbol to use
print("\n3. Determining best symbol for analysis...")

# Check record counts for different symbol patterns
cursor.execute("""
    SELECT 
        CASE 
            WHEN symbol = 'NIFTY 50' THEN 'NIFTY 50'
            WHEN symbol = 'NIFTY' THEN 'NIFTY'
            WHEN symbol LIKE 'NIFTY%FUT' THEN 'NIFTY Futures'
            ELSE 'Other'
        END as symbol_type,
        COUNT(*) as count,
        MIN(ts) as earliest,
        MAX(ts) as latest
    FROM ltp_ticks
    WHERE symbol LIKE 'NIFTY%'
    GROUP BY symbol_type
    ORDER BY count DESC
""")

symbol_stats = cursor.fetchall()
print("\nSymbol Statistics:")
for stat in symbol_stats:
    print(f"   {stat[0]:20} {stat[1]:>10,} records  ({stat[2]} to {stat[3]})")

conn.close()

print("\n" + "=" * 80)
print("NEXT STEPS:")
print("=" * 80)
print("1. Identify the correct spot/futures symbol to use")
print("2. Build 20s and 30s candles from 5s ticks")
print("3. Detect continuation patterns (next 1-2 candles same direction)")
print("4. Count signal frequency")
print("5. Correlate with futures Greeks and IV")
print("=" * 80)
