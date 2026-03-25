#!/usr/bin/env python3
"""
Verify ltp_ticks timestamps in local PostgreSQL against NSE trading hours (09:15–15:30 IST).
Read-only: no changes to database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

QUERY = """
SELECT
  count(*) FILTER (WHERE (ts AT TIME ZONE 'Asia/Kolkata')::time < '09:15:00'
                   OR (ts AT TIME ZONE 'Asia/Kolkata')::time > '15:30:00') AS outside_hours,
  count(*) FILTER (WHERE (ts AT TIME ZONE 'Asia/Kolkata')::time >= '09:15:00'
                   AND (ts AT TIME ZONE 'Asia/Kolkata')::time <= '15:30:00') AS inside_hours,
  count(*) AS total
FROM ltp_ticks;
"""

DATES_QUERY = """
SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d, count(*) AS cnt
FROM ltp_ticks
WHERE (ts AT TIME ZONE 'Asia/Kolkata')::time < '09:15:00'
   OR (ts AT TIME ZONE 'Asia/Kolkata')::time > '15:30:00'
GROUP BY 1 ORDER BY 1;
"""


def main():
    try:
        conn = db.get_connection()
    except Exception as e:
        print(f"Database connection failed: {e}", file=sys.stderr)
        return 1
    if not db.table_exists(conn, "ltp_ticks"):
        print("ltp_ticks table not found.", file=sys.stderr)
        conn.close()
        return 1
    with conn.cursor() as cur:
        cur.execute(QUERY)
        outside, inside, total = cur.fetchone()
        outside = outside or 0
        inside = inside or 0
        total = total or 0
    print("=== Local PostgreSQL ltp_ticks: Trading Hours (09:15–15:30 IST) ===")
    print("Outside hours (<09:15 or >15:30 IST):", f"{outside:,}")
    print("Inside hours (09:15–15:30 IST):      ", f"{inside:,}")
    print("Total rows:                          ", f"{total:,}")
    if total > 0:
        print("Outside-hours %:                     ", f"{100 * outside / total:.2f}%")
    with conn.cursor() as cur:
        cur.execute(DATES_QUERY)
        rows = cur.fetchall()
    if rows:
        print(f"\nDates with out-of-market data: {len(rows)}")
        for r in rows[:15]:
            print(f"  {r[0]}: {r[1]:,} rows")
        if len(rows) > 15:
            print(f"  ... and {len(rows) - 15} more dates")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
