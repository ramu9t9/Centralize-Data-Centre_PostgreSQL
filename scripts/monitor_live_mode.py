#!/usr/bin/env python3
"""
Live Mode Monitor - Shows real-time broadcast activity (PostgreSQL)
"""
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

print("=" * 70)
print("LIVE MODE MONITOR - Real-time Data Broadcasting (PostgreSQL)")
print("=" * 70)
print("Database: DATABASE_URL")
print("Monitoring for new records every 5 seconds...")
print("=" * 70)
print()

last_ts = None
record_count = 0

try:
    while True:
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Table not found, waiting...")
                time.sleep(5)
                continue
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
            latest_ts = cursor.fetchone()[0]
            conn.close()

            if latest_ts is not None:
                latest_ts_str = str(latest_ts).replace(' ', 'T')[:19]
                if last_ts is None:
                    last_ts = latest_ts_str
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Initial timestamp: {latest_ts_str}")
                elif latest_ts_str > last_ts:
                    conn = db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM ltp_ticks WHERE ts > %s::timestamptz", (last_ts,))
                    new_count = cursor.fetchone()[0]
                    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM ltp_ticks WHERE ts = %s::timestamptz", (latest_ts_str,))
                    symbols = cursor.fetchone()[0]
                    conn.close()
                    record_count += new_count
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] NEW DATA: {new_count} records | "
                          f"Latest: {latest_ts_str} | Symbols: {symbols} | Total broadcasted: {record_count}")
                    last_ts = latest_ts_str
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for new data... (Last: {latest_ts_str})")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] No data in database yet...")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}")
        
        time.sleep(5)
except KeyboardInterrupt:
    print("\nMonitor stopped.")
