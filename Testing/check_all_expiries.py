import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
cursor = conn.cursor()

print("All available expiries on Dec 30, 2025:")
cursor.execute("""
    SELECT DISTINCT substring(symbol from 6 for 7) as expiry, COUNT(*) as count
    FROM ltp_ticks
    WHERE symbol LIKE 'NIFTY%%'
      AND symbol != 'NIFTY 50'
      AND symbol NOT LIKE '%%FUT'
      AND ts::date = '2025-12-30'
    GROUP BY substring(symbol from 6 for 7)
    ORDER BY expiry
""")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]:,} records")

print("\nSample symbols for each expiry:")
cursor.execute("""
    SELECT DISTINCT substring(symbol from 6 for 7) as expiry
    FROM ltp_ticks
    WHERE symbol LIKE 'NIFTY%%'
      AND symbol != 'NIFTY 50'
      AND symbol NOT LIKE '%%FUT'
      AND ts::date = '2025-12-30'
    ORDER BY expiry
""")
for row in cursor.fetchall():
    expiry = row[0]
    cursor.execute("""
        SELECT symbol FROM ltp_ticks
        WHERE symbol LIKE %s
          AND ts::date = '2025-12-30'
        LIMIT 3
    """, (f"NIFTY{expiry}%",))
    print(f"\n{expiry}:")
    for sym in cursor.fetchall():
        print(f"  {sym[0]}")

conn.close()
