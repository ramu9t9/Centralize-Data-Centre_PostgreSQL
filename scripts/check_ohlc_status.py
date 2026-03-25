#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM ohlc_1min")
cnt = cur.fetchone()[0]
cur.execute("SELECT MIN(ts), MAX(ts) FROM ohlc_1min")
min_ts, max_ts = cur.fetchone()
# IST wall-clock time (session TZ is Asia/Kolkata in db.get_connection)
cur.execute("""
  SELECT count(*) FILTER (WHERE (ts AT TIME ZONE 'Asia/Kolkata')::time < '09:15:00'
                          OR (ts AT TIME ZONE 'Asia/Kolkata')::time > '15:30:00') AS outside,
         count(*) FILTER (WHERE (ts AT TIME ZONE 'Asia/Kolkata')::time >= '09:15:00'
                          AND (ts AT TIME ZONE 'Asia/Kolkata')::time <= '15:30:00') AS inside
  FROM ohlc_1min
""")
outside, inside = cur.fetchone()
print("ohlc_1min status:")
print(f"  Total rows:     {cnt:,}")
print(f"  Date range:     {min_ts} to {max_ts}")
print(f"  Inside hours:   {inside:,}")
print(f"  Outside hours:  {outside:,}")
conn.close()
