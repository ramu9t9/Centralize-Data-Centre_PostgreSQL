#!/usr/bin/env python3
"""
Build 1-minute OHLC from ltp_ticks into ohlc_1min (IST).

Reads tick data from ltp_ticks (TIMESTAMPTZ/UTC), aggregates to 1-minute bars
in Asia/Kolkata, and upserts into ohlc_1min (ts = TIMESTAMP WITHOUT TIME ZONE, IST).

Modes:
  - Continue (default): Resume from last built minute; process only new data.
  - Full rebuild (--rebuild): Truncate ohlc_1min and rebuild from all ltp_ticks.

Run after syncing ltp_ticks from VPS. Safe to run anytime.
"""

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

IST_TZ = "Asia/Kolkata"
IST_OFFSET = timedelta(hours=5, minutes=30)


def date_to_utc_range(d: date):
    """Return (ts_lo, ts_hi) as UTC timestamp strings for IST date d (whole day)."""
    # 00:00 IST on d = (d-1) 18:30 UTC; 00:00 IST on d+1 = d 18:30 UTC
    ts_lo = datetime.combine(d, time(0, 0, 0)) - IST_OFFSET
    ts_hi = datetime.combine(d, time(0, 0, 0)) + timedelta(days=1) - IST_OFFSET
    lo_str = ts_lo.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    hi_str = ts_hi.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    return lo_str, hi_str


def get_last_ohlc_ts(conn):
    """Return the last ts (IST minute) in ohlc_1min, or None if empty."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(ts) FROM ohlc_1min")
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_dates_to_process(conn, after_ts_ist=None):
    """
    Return list of distinct IST dates from ltp_ticks that need processing.
    If after_ts_ist is None, return all dates; else only dates with data after that minute.
    """
    with conn.cursor() as cur:
        if after_ts_ist is None:
            cur.execute(
                """
                SELECT DISTINCT (ts AT TIME ZONE %s)::date
                FROM ltp_ticks
                ORDER BY 1
                """,
                (IST_TZ,),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT (ts AT TIME ZONE %s)::date
                FROM ltp_ticks
                WHERE date_trunc('minute', ts AT TIME ZONE %s)::timestamp without time zone > %s
                ORDER BY 1
                """,
                (IST_TZ, IST_TZ, after_ts_ist),
            )
        return [row[0] for row in cur.fetchall()]


def count_ticks(conn, d: date, after_ts_ist=None):
    """Count ltp_ticks rows for date d (IST) in UTC range; optionally only after after_ts_ist."""
    ts_lo, ts_hi = date_to_utc_range(d)
    with conn.cursor() as cur:
        if after_ts_ist is None:
            cur.execute(
                """
                SELECT COUNT(*) FROM ltp_ticks
                WHERE ts >= %s::timestamptz AND ts < %s::timestamptz AND ltp IS NOT NULL
                """,
                (ts_lo, ts_hi),
            )
        else:
            cur.execute(
                """
                SELECT COUNT(*) FROM ltp_ticks
                WHERE ts >= %s::timestamptz AND ts < %s::timestamptz AND ltp IS NOT NULL
                  AND date_trunc('minute', ts AT TIME ZONE %s)::timestamp without time zone > %s
                """,
                (ts_lo, ts_hi, IST_TZ, after_ts_ist),
            )
        return cur.fetchone()[0]


def aggregate_and_upsert(conn, d: date, after_ts_ist=None):
    """
    Load ltp_ticks for date d (UTC range), aggregate to 1-min in IST, upsert into ohlc_1min.
    Returns number of rows upserted.
    """
    ts_lo, ts_hi = date_to_utc_range(d)
    sql = """
        SELECT symbol, ts, ltp, volume, oi, delta, iv
        FROM ltp_ticks
        WHERE ts >= %s::timestamptz AND ts < %s::timestamptz AND ltp IS NOT NULL
        ORDER BY symbol, ts
    """
    df = pd.read_sql(sql, conn, params=[ts_lo, ts_hi])
    if df.empty:
        return 0

    # Convert ts to IST and floor to minute (no timezone in result for ohlc_1min.ts)
    df["ts_ist"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(IST_TZ).dt.tz_localize(None)
    df["bucket"] = df["ts_ist"].dt.floor("min")

    agg = df.groupby(["symbol", "bucket"], as_index=False).agg(
        open=("ltp", "first"),
        high=("ltp", "max"),
        low=("ltp", "min"),
        close=("ltp", "last"),
        volume=("volume", "sum"),
        oi=("oi", "last"),
        delta=("delta", "last"),
        iv=("iv", "last"),
    )
    agg = agg.rename(columns={"bucket": "ts"})

    if after_ts_ist is not None:
        agg = agg[agg["ts"] > after_ts_ist]
    if agg.empty:
        return 0

    # Ensure types for PostgreSQL
    agg["volume"] = agg["volume"].fillna(0).astype("int64")

    upsert_sql = """
        INSERT INTO ohlc_1min (symbol, ts, open, high, low, close, volume, oi, delta, iv)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, ts) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
            volume = EXCLUDED.volume, oi = EXCLUDED.oi, delta = EXCLUDED.delta, iv = EXCLUDED.iv
    """
    rows = []
    for r in agg.itertuples(index=False):
        rows.append(
            (
                r.symbol,
                r.ts,
                r.open,
                r.high,
                r.low,
                r.close,
                int(r.volume),
                int(r.oi) if pd.notna(r.oi) else None,
                float(r.delta) if pd.notna(r.delta) else None,
                float(r.iv) if pd.notna(r.iv) else None,
            )
        )
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, upsert_sql, rows, page_size=1000)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Build 1-min OHLC from ltp_ticks into ohlc_1min (IST)."
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Truncate ohlc_1min and rebuild entirely from ltp_ticks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip tick count per date (faster)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process only first N dates (for testing)",
    )
    args = parser.parse_args()

    try:
        conn = db.get_connection()
    except Exception as e:
        print(f"Database connection failed: {e}")
        return 1

    if not db.table_exists(conn, "ltp_ticks"):
        print("ltp_ticks table not found. Run dry_run_postgres.py first.")
        conn.close()
        return 1
    if not db.table_exists(conn, "ohlc_1min"):
        print("ohlc_1min table not found. Run dry_run_postgres.py first.")
        conn.close()
        return 1

    try:
        if args.rebuild:
            dates = get_dates_to_process(conn, None)
            if args.dry_run:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM ltp_ticks WHERE ltp IS NOT NULL")
                    cnt = cur.fetchone()[0]
                print(f"[dry-run] Would TRUNCATE ohlc_1min and rebuild from {cnt:,} ticks ({len(dates)} dates).")
                return 0
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE ohlc_1min")
            conn.commit()
            if not dates:
                print("No ltp_ticks data.")
                return 0
            print(f"Rebuilding ohlc_1min. Processing {len(dates)} date(s)...")
            total_ticks = 0
            total_rows = 0
            for i, d in enumerate(dates):
                if args.limit and i >= args.limit:
                    break
                ticks = 0 if args.fast else count_ticks(conn, d, None)
                n = aggregate_and_upsert(conn, d, None)
                total_ticks += ticks
                total_rows += n
                if args.fast:
                    print(f"  [{i+1}/{len(dates)}] {d}: {n:,} 1-min rows")
                else:
                    print(f"  [{i+1}/{len(dates)}] {d}: {ticks:,} ticks -> {n:,} 1-min rows")
                conn.commit()
            if args.fast:
                print(f"Done. Rebuilt {total_rows:,} 1-min rows.")
            else:
                print(f"Done. {total_ticks:,} ticks -> {total_rows:,} 1-min rows.")
        else:
            last_ts = get_last_ohlc_ts(conn)
            dates = get_dates_to_process(conn, last_ts)
            if not dates:
                print("No new ltp_ticks to process. ohlc_1min is up to date.")
                return 0
            if args.dry_run:
                print(f"[dry-run] Would process {len(dates)} date(s) from {dates[0]} to {dates[-1]}.")
                return 0
            print(f"Processing {len(dates)} date(s) from {dates[0]} to {dates[-1]}...")
            total_ticks = 0
            total_rows = 0
            for i, d in enumerate(dates):
                if args.limit and i >= args.limit:
                    break
                ticks = 0 if args.fast else count_ticks(conn, d, last_ts)
                n = aggregate_and_upsert(conn, d, last_ts)
                total_ticks += ticks
                total_rows += n
                if args.fast:
                    print(f"  [{i+1}/{len(dates)}] {d}: {n:,} 1-min rows")
                else:
                    print(f"  [{i+1}/{len(dates)}] {d}: {ticks:,} ticks -> {n:,} 1-min rows")
                conn.commit()
                if last_ts and d >= last_ts.date():
                    last_ts = None
            if args.fast:
                print(f"Done. Inserted {total_rows:,} 1-min rows.")
            else:
                print(f"Done. {total_ticks:,} ticks -> {total_rows:,} 1-min rows.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
