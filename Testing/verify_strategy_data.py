import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
cursor = conn.cursor()

print("=" * 80)
print("DATABASE SCHEMA VERIFICATION (PostgreSQL)")
print("=" * 80)

# 1. Check actual columns
print("\n1. ACTUAL DATABASE COLUMNS:")
cursor.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'ltp_ticks'
    ORDER BY ordinal_position
""")
for row in cursor.fetchall():
    print(f"   {row[0]:15} {row[1]:10} {'NULL' if row[2] == 'YES' else 'NOT NULL'}")

# 2. Check for futures symbols
print("\n2. FUTURES SYMBOLS (Sample):")
cursor.execute("SELECT DISTINCT symbol FROM ltp_ticks WHERE symbol LIKE '%%FUT%%' LIMIT 5")
futures = cursor.fetchall()
if futures:
    for row in futures:
        print(f"   ✅ {row[0]}")
else:
    print("   ❌ NO FUTURES FOUND")

# 3. Check for options symbols
print("\n3. OPTIONS SYMBOLS (Sample):")
cursor.execute("SELECT DISTINCT symbol FROM ltp_ticks WHERE symbol LIKE '%%CE' OR symbol LIKE '%%PE' LIMIT 5")
options = cursor.fetchall()
if options:
    for row in options:
        print(f"   ✅ {row[0]}")
else:
    print("   ❌ NO OPTIONS FOUND")

# 4. Sample option data
print("\n4. SAMPLE OPTION DATA (1 record):")
cursor.execute("""
    SELECT symbol, ltp, bid, ask, volume, oi, delta, gamma, theta, vega, iv 
    FROM ltp_ticks 
    WHERE (symbol LIKE '%%CE' OR symbol LIKE '%%PE')
    AND ltp IS NOT NULL 
    LIMIT 1
""")
sample = cursor.fetchone()
if sample:
    print(f"   Symbol: {sample[0]}")
    print(f"   LTP: {sample[1]}")
    print(f"   Bid: {sample[2]}")
    print(f"   Ask: {sample[3]}")
    print(f"   Volume: {sample[4]}")
    print(f"   OI: {sample[5]}")
    print(f"   Delta: {sample[6]}")
    print(f"   Gamma: {sample[7]}")
    print(f"   Theta: {sample[8]}")
    print(f"   Vega: {sample[9]}")
    print(f"   IV: {sample[10]}")
else:
    print("   ❌ NO DATA FOUND")

# 5. Database statistics
print("\n5. DATABASE STATISTICS:")
cursor.execute("SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(ts), MAX(ts) FROM ltp_ticks")
stats = cursor.fetchone()
print(f"   Total Symbols: {stats[0]:,}")
print(f"   Total Records: {stats[1]:,}")
print(f"   Date Range: {stats[2]} to {stats[3]}")

# 6. Data completeness
print("\n6. DATA COMPLETENESS CHECK:")
cursor.execute("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN ltp IS NOT NULL THEN 1 ELSE 0 END) as has_ltp,
        SUM(CASE WHEN bid IS NOT NULL THEN 1 ELSE 0 END) as has_bid,
        SUM(CASE WHEN ask IS NOT NULL THEN 1 ELSE 0 END) as has_ask,
        SUM(CASE WHEN volume IS NOT NULL THEN 1 ELSE 0 END) as has_volume,
        SUM(CASE WHEN oi IS NOT NULL THEN 1 ELSE 0 END) as has_oi,
        SUM(CASE WHEN delta IS NOT NULL THEN 1 ELSE 0 END) as has_delta,
        SUM(CASE WHEN iv IS NOT NULL AND iv > 0 THEN 1 ELSE 0 END) as has_iv
    FROM ltp_ticks
    WHERE symbol LIKE '%%CE' OR symbol LIKE '%%PE'
""")
completeness = cursor.fetchone()
total = completeness[0]
if total > 0:
    print(f"   Options Records: {total:,}")
    print(f"   Has LTP: {completeness[1]:,} ({completeness[1]/total*100:.1f}%)")
    print(f"   Has Bid: {completeness[2]:,} ({completeness[2]/total*100:.1f}%)")
    print(f"   Has Ask: {completeness[3]:,} ({completeness[3]/total*100:.1f}%)")
    print(f"   Has Volume: {completeness[4]:,} ({completeness[4]/total*100:.1f}%)")
    print(f"   Has OI: {completeness[5]:,} ({completeness[5]/total*100:.1f}%)")
    print(f"   Has Delta: {completeness[6]:,} ({completeness[6]/total*100:.1f}%)")
    print(f"   Has IV: {completeness[7]:,} ({completeness[7]/total*100:.1f}%)")

print("\n" + "=" * 80)
conn.close()
