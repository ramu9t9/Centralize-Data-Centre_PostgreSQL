import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()

test_ts = '2025-12-30 03:45:20'
test_spot = 25905.50
test_strike = round(test_spot / 50) * 50

print("Test Signal:")
print(f"  Timestamp: {test_ts}")
print(f"  Spot: {test_spot}")
print(f"  ATM Strike: {test_strike}")

cursor = conn.cursor()
cursor.execute("""
    SELECT DISTINCT substring(symbol from 6 for 7) as expiry
    FROM ltp_ticks
    WHERE symbol LIKE 'NIFTY%%'
      AND symbol != 'NIFTY 50'
      AND symbol NOT LIKE '%%FUT'
      AND (symbol LIKE '%%CE' OR symbol LIKE '%%PE')
      AND ts::date = '2025-12-30'
    ORDER BY expiry
    LIMIT 1
""")
expiry = cursor.fetchone()[0]
print(f"  Expiry: {expiry}")

ce_symbol = f"NIFTY{expiry}{int(test_strike)}CE"
pe_symbol = f"NIFTY{expiry}{int(test_strike)}PE"
print(f"\nLooking for:\n  CE: {ce_symbol}\n  PE: {pe_symbol}")

cursor.execute("SELECT COUNT(*) FROM ltp_ticks WHERE symbol = %s AND ts::date = '2025-12-30'", (ce_symbol,))
ce_count = cursor.fetchone()[0]
print(f"\nCE records for this date: {ce_count}")

if ce_count > 0:
    cursor.execute("SELECT MIN(ts), MAX(ts) FROM ltp_ticks WHERE symbol = %s AND ts::date = '2025-12-30'", (ce_symbol,))
    ce_range = cursor.fetchone()
    print(f"CE time range: {ce_range[0]} to {ce_range[1]}")
    ce_query = """
        SELECT ts, ltp, delta, iv
        FROM ltp_ticks
        WHERE symbol = %s
          AND ts >= %s::timestamptz - interval '5 minutes'
          AND ts <= %s::timestamptz + interval '5 minutes'
        ORDER BY ABS(EXTRACT(EPOCH FROM (ts - %s::timestamptz)))
        LIMIT 1
    """
    ce_data = pd.read_sql_query(ce_query, conn, params=(ce_symbol, test_ts, test_ts, test_ts))
    print("\nResult:")
    print(ce_data)

conn.close()
