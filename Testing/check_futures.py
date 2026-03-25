import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
c = conn.cursor()

print("Futures symbols on Dec 30, 2025:")
c.execute("SELECT DISTINCT symbol FROM ltp_ticks WHERE symbol LIKE '%%FUT%%' AND ts::date = '2025-12-30'")
for row in c.fetchall():
    print(f"  {row[0]}")

print("\nSample futures data:")
c.execute("SELECT symbol, ts, ltp, delta, iv FROM ltp_ticks WHERE symbol LIKE '%%FUT%%' AND ts::date = '2025-12-30' LIMIT 3")
for row in c.fetchall():
    print(f"  {row}")

conn.close()
