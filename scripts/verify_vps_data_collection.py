#!/usr/bin/env python3
"""Verify VPS data collection - check all data types (PostgreSQL)"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

try:
    conn = db.get_connection()
except Exception as e:
    print("❌ Cannot connect to database:", e)
    print("Set DATABASE_URL (e.g. postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data)")
    sys.exit(1)

if not db.table_exists(conn, "ltp_ticks"):
    print("❌ Table ltp_ticks not found!")
    conn.close()
    sys.exit(1)

cursor = conn.cursor()

print("=" * 70)
print("VPS DATA COLLECTION VERIFICATION (PostgreSQL)")
print("=" * 70)
print()

# Overall statistics
cursor.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(DISTINCT symbol) as unique_symbols,
        MIN(ts) as earliest,
        MAX(ts) as latest
    FROM ltp_ticks
""")
stats = cursor.fetchone()
total, unique_symbols, earliest, latest = stats

print("📊 OVERALL STATISTICS:")
print(f"  Total records: {total:,}")
print(f"  Unique symbols: {unique_symbols}")
if earliest is not None:
    print(f"  Earliest record: {earliest}")
if latest is not None:
    print(f"  Latest record: {latest}")
print()

# Helper for ts display
def ts_str(ts):
    if ts is None:
        return "N/A"
    s = str(ts)
    return s[:19] if len(s) >= 19 else s

# Check by symbol type
print("📊 DATA BY SYMBOL TYPE:")
print()

# 1. NIFTY 50 Index
cursor.execute("""
    SELECT 
        COUNT(*) as count,
        MAX(ts) as latest_ts,
        MAX(ltp) as latest_ltp,
        COUNT(CASE WHEN volume IS NOT NULL AND volume > 0 THEN 1 END) as with_volume,
        COUNT(CASE WHEN oi IS NOT NULL AND oi > 0 THEN 1 END) as with_oi
    FROM ltp_ticks
    WHERE symbol = 'NIFTY 50'
""")
nifty50 = cursor.fetchone()
if nifty50[0] > 0:
    print("1️⃣  NIFTY 50 INDEX:")
    print(f"    Records: {nifty50[0]:,}")
    print(f"    Latest: {ts_str(nifty50[1])}")
    print(f"    Latest LTP: {nifty50[2]}")
    print(f"    With Volume: {nifty50[3]} (Index has no volume - expected)")
    print(f"    With OI: {nifty50[4]} (Index has no OI - expected)")
    print()
    cursor.execute("""
        SELECT ts, ltp, volume, oi, delta, gamma, theta, vega, iv, source
        FROM ltp_ticks
        WHERE symbol = 'NIFTY 50'
        ORDER BY ts DESC
        LIMIT 1
    """)
    sample = cursor.fetchone()
    if sample:
        print("    Sample Record:")
        print(f"      Time: {ts_str(sample[0])}")
        print(f"      LTP: {sample[1]}")
        print(f"      Volume: {sample[2] if sample[2] is not None else 'NULL'} (Index)")
        print(f"      OI: {sample[3] if sample[3] is not None else 'NULL'} (Index)")
        print(f"      Delta: {sample[4] if sample[4] is not None else 'NULL'} (Index)")
        print(f"      IV: {sample[5] if sample[5] is not None else 'NULL'} (Index)")
        print()
else:
    print("1️⃣  NIFTY 50 INDEX: ❌ No data found")
    print()

# 2. Futures
cursor.execute("""
    SELECT 
        COUNT(*) as count,
        COUNT(DISTINCT symbol) as unique_futures,
        MAX(ts) as latest_ts,
        COUNT(CASE WHEN volume > 0 THEN 1 END) as with_volume,
        COUNT(CASE WHEN oi > 0 THEN 1 END) as with_oi,
        COUNT(CASE WHEN delta IS NOT NULL THEN 1 END) as with_delta,
        COUNT(CASE WHEN iv IS NOT NULL AND iv > 0 THEN 1 END) as with_iv
    FROM ltp_ticks
    WHERE symbol LIKE '%%FUT%%'
""")
futures = cursor.fetchone()
if futures[0] > 0:
    print("2️⃣  FUTURES:")
    print(f"    Total records: {futures[0]:,}")
    print(f"    Unique futures: {futures[1]}")
    print(f"    Latest: {ts_str(futures[2])}")
    print(f"    With Volume: {futures[3]:,} ({futures[3]/futures[0]*100:.1f}%)")
    print(f"    With OI: {futures[4]:,} ({futures[4]/futures[0]*100:.1f}%)")
    print(f"    With Delta: {futures[5]:,} ({futures[5]/futures[0]*100:.1f}%)")
    print(f"    With IV: {futures[6]:,} ({futures[6]/futures[0]*100:.1f}%)")
    print()
    cursor.execute("""
        SELECT symbol, ts, ltp, volume, oi, delta, gamma, theta, vega, iv
        FROM ltp_ticks
        WHERE symbol LIKE '%%FUT%%'
        ORDER BY ts DESC
        LIMIT 3
    """)
    print("    Sample Futures Records:")
    for row in cursor.fetchall():
        symbol, ts, ltp, vol, oi, delta, gamma, theta, vega, iv = row
        print(f"      {symbol:30} | {ts_str(ts)} | LTP: {ltp:>8.2f}")
        print(f"        Vol: {vol if vol else 'NULL':>12}, OI: {oi if oi else 'NULL':>12}")
        print(f"        Δ: {delta if delta else 'NULL':>8}, IV: {iv if iv else 'NULL':>8}")
    print()
else:
    print("2️⃣  FUTURES: ⚠️  No futures data found")
    print()

# 3. Options (CE/PE)
cursor.execute("""
    SELECT 
        COUNT(*) as count,
        COUNT(DISTINCT symbol) as unique_options,
        MAX(ts) as latest_ts,
        COUNT(CASE WHEN volume > 0 THEN 1 END) as with_volume,
        COUNT(CASE WHEN oi > 0 THEN 1 END) as with_oi,
        COUNT(CASE WHEN delta IS NOT NULL AND delta != 0 THEN 1 END) as with_delta,
        COUNT(CASE WHEN gamma IS NOT NULL AND gamma != 0 THEN 1 END) as with_gamma,
        COUNT(CASE WHEN theta IS NOT NULL AND theta != 0 THEN 1 END) as with_theta,
        COUNT(CASE WHEN vega IS NOT NULL AND vega != 0 THEN 1 END) as with_vega,
        COUNT(CASE WHEN iv IS NOT NULL AND iv > 0 THEN 1 END) as with_iv
    FROM ltp_ticks
    WHERE (symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%')
""")
options = cursor.fetchone()
if options[0] > 0:
    print("3️⃣  OPTIONS (CE/PE):")
    print(f"    Total records: {options[0]:,}")
    print(f"    Unique options: {options[1]}")
    print(f"    Latest: {ts_str(options[2])}")
    print(f"    With Volume: {options[3]:,} ({options[3]/options[0]*100:.1f}%)")
    print(f"    With OI: {options[4]:,} ({options[4]/options[0]*100:.1f}%)")
    print(f"    With Delta: {options[5]:,} ({options[5]/options[0]*100:.1f}%)")
    print(f"    With Gamma: {options[6]:,} ({options[6]/options[0]*100:.1f}%)")
    print(f"    With Theta: {options[7]:,} ({options[7]/options[0]*100:.1f}%)")
    print(f"    With Vega: {options[8]:,} ({options[8]/options[0]*100:.1f}%)")
    print(f"    With IV: {options[9]:,} ({options[9]/options[0]*100:.1f}%)")
    print()
    cursor.execute("""
        SELECT symbol, ts, ltp, volume, oi, delta, gamma, theta, vega, iv
        FROM ltp_ticks
        WHERE (symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%')
        AND delta IS NOT NULL
        AND iv IS NOT NULL
        AND iv > 0
        ORDER BY ts DESC
        LIMIT 5
    """)
    print("    Sample Options with Greeks/IV:")
    for row in cursor.fetchall():
        symbol, ts, ltp, vol, oi, delta, gamma, theta, vega, iv = row
        print(f"      {symbol:35} | {ts_str(ts)}")
        print(f"        LTP: {ltp:>8.2f} | Vol: {vol if vol else 'NULL':>12,} | OI: {oi if oi else 'NULL':>12,}")
        print(f"        Δ: {delta:>8.4f} | Γ: {gamma:>8.4f} | Θ: {theta:>8.4f} | ν: {vega:>8.4f} | IV: {iv:>6.2f}%")
    print()
    # Recent options without Greeks - use PostgreSQL interval
    cursor.execute("""
        SELECT COUNT(*) 
        FROM ltp_ticks
        WHERE (symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%')
        AND ts >= (NOW() AT TIME ZONE 'UTC' - INTERVAL '5 minutes')
        AND (delta IS NULL OR delta = 0)
    """)
    no_greeks = cursor.fetchone()[0]
    if no_greeks > 0:
        print(f"    ⚠️  {no_greeks} recent option records without Greeks (may be updating)")
    print()
else:
    print("3️⃣  OPTIONS: ❌ No options data found")
    print()

# 4. Recent data (last 5 minutes)
print("📊 RECENT DATA (Last 5 minutes):")
cursor.execute("""
    SELECT 
        COUNT(*) as recent_count,
        COUNT(DISTINCT symbol) as recent_symbols,
        COUNT(CASE WHEN symbol = 'NIFTY 50' THEN 1 END) as nifty50_count,
        COUNT(CASE WHEN symbol LIKE '%%FUT%%' THEN 1 END) as futures_count,
        COUNT(CASE WHEN symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%' THEN 1 END) as options_count,
        COUNT(CASE WHEN (symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%') AND delta IS NOT NULL AND iv > 0 THEN 1 END) as options_with_greeks
    FROM ltp_ticks
    WHERE ts >= (NOW() AT TIME ZONE 'UTC' - INTERVAL '5 minutes')
""")
recent = cursor.fetchone()
if recent[0] > 0:
    print(f"  Total records: {recent[0]:,}")
    print(f"  Unique symbols: {recent[1]}")
    print(f"  NIFTY 50: {recent[2]:,} records")
    print(f"  Futures: {recent[3]:,} records")
    print(f"  Options: {recent[4]:,} records")
    print(f"  Options with Greeks/IV: {recent[5]:,} records")
    print()
else:
    print("  ⚠️  No recent data (service may not be running or market closed)")
    print()

# 5. Data source
print("📊 DATA SOURCE:")
cursor.execute("""
    SELECT source, COUNT(*) as count
    FROM ltp_ticks
    GROUP BY source
""")
sources = cursor.fetchall()
for source, count in sources:
    print(f"  {source or 'NULL'}: {count:,} records")
print()

# 6. Summary
print("=" * 70)
print("✅ DATA COLLECTION SUMMARY")
print("=" * 70)

checks = []
checks.append(("NIFTY 50 Index", nifty50[0] > 0 if nifty50 else False))
checks.append(("Futures", futures[0] > 0 if futures else False))
checks.append(("Options", options[0] > 0 if options else False))
checks.append(("Options with Volume", options[3] > 0 if options else False))
checks.append(("Options with OI", options[4] > 0 if options else False))
checks.append(("Options with Delta", options[5] > 0 if options else False))
checks.append(("Options with Gamma", options[6] > 0 if options else False))
checks.append(("Options with Theta", options[7] > 0 if options else False))
checks.append(("Options with Vega", options[8] > 0 if options else False))
checks.append(("Options with IV", options[9] > 0 if options else False))

for check_name, status in checks:
    status_icon = "✅" if status else "❌"
    print(f"  {status_icon} {check_name}")

print()
print("=" * 70)

conn.close()
