import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
cursor = conn.cursor()

print("Checking data availability by date...")
cursor.execute("SELECT (ts::date)::text as d, COUNT(*) FROM ltp_ticks WHERE symbol = 'NIFTY 50' GROUP BY ts::date ORDER BY ts::date")
for row in cursor.fetchall():
    print(f"Date: {row[0]}, Count: {row[1]}")

conn.close()
