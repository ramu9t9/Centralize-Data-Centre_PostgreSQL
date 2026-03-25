import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
c = conn.cursor()

print("Checking futures data availability...")
c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM ltp_ticks WHERE symbol = 'NIFTY24FEB26FUT' AND ts::date = '2025-12-30'")
result = c.fetchone()
print(f"Futures records: {result[0]}, Range: {result[1]} to {result[2]}")

if result[0] > 0:
    print("\nSample futures data:")
    c.execute("SELECT ts, ltp, delta, gamma, theta, vega, iv FROM ltp_ticks WHERE symbol = 'NIFTY24FEB26FUT' AND ts::date = '2025-12-30' LIMIT 5")
    for row in c.fetchall():
        print(f"  {row}")

print("\nChecking options data...")
c.execute("SELECT COUNT(*) FROM ltp_ticks WHERE (symbol LIKE '%%CE' OR symbol LIKE '%%PE') AND ts::date = '2025-12-30'")
print(f"Options records: {c.fetchone()[0]}")

c.execute("SELECT DISTINCT symbol FROM ltp_ticks WHERE (symbol LIKE '%%25900CE' OR symbol LIKE '%%25900PE') AND ts::date = '2025-12-30' LIMIT 5")
print("\nSample option symbols (25900 strike):")
for row in c.fetchall():
    print(f"  {row[0]}")

conn.close()
