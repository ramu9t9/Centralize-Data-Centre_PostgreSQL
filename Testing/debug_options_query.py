import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()

test_ts = '2025-12-30 04:00:00'
test_strike = 25900

print(f"Testing ATM strike {test_strike} at {test_ts}")

ce_query = """
    SELECT symbol, ts, ltp, delta, iv
    FROM ltp_ticks
    WHERE symbol LIKE %s
      AND ts::date = '2025-12-30'
      AND ts BETWEEN %s::timestamptz - interval '300 seconds'
                 AND %s::timestamptz + interval '300 seconds'
    ORDER BY ABS(EXTRACT(EPOCH FROM (ts - %s::timestamptz)))
    LIMIT 5
"""
print("\nCE Query Results:")
ce_data = pd.read_sql_query(ce_query, conn, params=(f'%{test_strike}CE', test_ts, test_ts, test_ts))
print(ce_data)

pe_query = """
    SELECT symbol, ts, ltp, delta, iv
    FROM ltp_ticks
    WHERE symbol LIKE %s
      AND ts::date = '2025-12-30'
      AND ts BETWEEN %s::timestamptz - interval '300 seconds'
                 AND %s::timestamptz + interval '300 seconds'
    ORDER BY ABS(EXTRACT(EPOCH FROM (ts - %s::timestamptz)))
    LIMIT 5
"""
print("\nPE Query Results:")
pe_data = pd.read_sql_query(pe_query, conn, params=(f'%{test_strike}PE', test_ts, test_ts, test_ts))
print(pe_data)

print("\nActual option symbols for this date:")
cursor = conn.cursor()
cursor.execute("SELECT DISTINCT symbol FROM ltp_ticks WHERE (symbol LIKE '%%CE' OR symbol LIKE '%%PE') AND ts::date = '2025-12-30' LIMIT 10")
for row in cursor.fetchall():
    print(f"  {row[0]}")

conn.close()
