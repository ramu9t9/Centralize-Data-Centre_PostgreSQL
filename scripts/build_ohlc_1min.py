#!/usr/bin/env python3
"""
Build 1-minute OHLC from 5-second ltp_ticks into ohlc_1min.

VPS-aligned: ohlc_1min.ts is TIMESTAMPTZ (minute start in Asia/Kolkata wall time).
Each bar's instant is the start of that IST minute (e.g. 09:15 IST).

Includes: OHLC (from ltp), volume, oi, delta, iv, gamma, theta, vega (last value per minute).
Symbol remapping: NIFTY -> NIFTY 50, BANKNIFTY -> NIFTY BANK.

Modes:
  - Continue (default): Resumes from last built minute; processes only new ltp_ticks data.
  - Full rebuild (--rebuild): Truncates ohlc_1min and rebuilds entirely from ltp_ticks.

Run after syncing ltp_ticks from VPS (typically every 3–5 days). Safe to run anytime.
"""

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

IST_TZ = "Asia/Kolkata"
# IST 00:00 = UTC previous-day 18:30
IST_OFFSET = timedelta(hours=5, minutes=30)

# ltp_ticks symbol -> ohlc_1min symbol (for consistency across data sources)
SYMBOL_REMAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
}


def date_to_ts_range_utc(d: date):
    """Return (ts_lo, ts_hi) in UTC for IST date d. Uses idx_ticks_ts."""
    # 00:00 IST on d = (d, 00:00) - 5:30 = (d-1, 18:30) UTC
    ts_lo = datetime.combine(d, time(0, 0, 0)) - IST_OFFSET
    ts_hi = datetime.combine(d, time(0, 0, 0)) + timedelta(days=1) - IST_OFFSET
    return ts_lo.strftime("%Y-%m-%d %H:%M:%S") + "+00", ts_hi.strftime("%Y-%m-%d %H:%M:%S") + "+00"


def get_last_ohlc_minute(conn) -> Optional[datetime]:
    """Return MAX(ts) from ohlc_1min (timestamptz), or None if empty."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(ts) FROM ohlc_1min")
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_min_max_dates_ist(conn, last_bar_ts: Optional[datetime]) -> list[date]:
    """
    Distinct IST calendar dates in ltp_ticks that need processing.
    If last_bar_ts is None, all dates. Else only dates with ticks after last_bar's IST minute.
    """
    with conn.cursor() as cur:
        if last_bar_ts is None:
            cur.execute("""
                SELECT DISTINCT (ts AT TIME ZONE %s)::date
                FROM ltp_ticks
                ORDER BY 1
            """, (IST_TZ,))
        else:
            cur.execute("""
                SELECT DISTINCT (ts AT TIME ZONE %s)::date
                FROM ltp_ticks
                WHERE date_trunc('minute', ts AT TIME ZONE %s)
                    > date_trunc('minute', %s::timestamptz AT TIME ZONE %s)
                ORDER BY 1
            """, (IST_TZ, IST_TZ, last_bar_ts, IST_TZ))
        return [row[0] for row in cur.fetchall()]


def count_ticks_for_date(conn, d: date, last_bar_ts: Optional[datetime]) -> int:
    """Count ltp_ticks rows for IST date d; optional filter for ticks after last_bar_ts minute."""
    ts_lo, ts_hi = date_to_ts_range_utc(d)
    if last_bar_ts is None:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM ltp_ticks
                WHERE ts >= %s::timestamptz AND ts < %s::timestamptz AND ltp IS NOT NULL
            """, (ts_lo, ts_hi))
            return cur.fetchone()[0]
    else:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM ltp_ticks
                WHERE ts >= %s::timestamptz AND ts < %s::timestamptz AND ltp IS NOT NULL
                AND date_trunc('minute', ts AT TIME ZONE %s)
                    > date_trunc('minute', %s::timestamptz AT TIME ZONE %s)
            """, (ts_lo, ts_hi, IST_TZ, last_bar_ts, IST_TZ))
            return cur.fetchone()[0]


def _last_bar_naive_ist_minute(last_bar_ts: datetime) -> pd.Timestamp:
    """IST wall-clock minute (naive) for comparing to floored tick buckets."""
    t = pd.Timestamp(last_bar_ts)
    if t.tzinfo is None:
        t = t.tz_localize(IST_TZ)
    else:
        t = t.tz_convert(IST_TZ)
    return t.replace(tzinfo=None).floor("min")


def run_aggregation_for_date(conn, d: date, last_bar_ts: Optional[datetime]) -> int:
    """Aggregate ltp_ticks in Python/pandas, then upsert. ts stored as TIMESTAMPTZ (IST minute start)."""
    ts_lo, ts_hi = date_to_ts_range_utc(d)
    sql = """
        SELECT symbol, ts, ltp, volume, oi, delta, iv, gamma, theta, vega
        FROM ltp_ticks
        WHERE ts >= %s::timestamptz AND ts < %s::timestamptz AND ltp IS NOT NULL
        ORDER BY symbol, ts
    """
    with conn.cursor() as cur:
        cur.execute(sql, (ts_lo, ts_hi))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return 0
    df["ts_ist"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(IST_TZ).dt.tz_localize(None)
    df["bucket"] = df["ts_ist"].dt.floor("min")
    df["symbol"] = df["symbol"].map(lambda s: SYMBOL_REMAP.get(s, s))
    agg = df.groupby(["symbol", "bucket"], as_index=False).agg(
        open=("ltp", "first"),
        high=("ltp", "max"),
        low=("ltp", "min"),
        close=("ltp", "last"),
        volume=("volume", "sum"),
        oi=("oi", "last"),
        delta=("delta", "last"),
        iv=("iv", "last"),
        gamma=("gamma", "last"),
        theta=("theta", "last"),
        vega=("vega", "last"),
    )
    agg = agg.rename(columns={"bucket": "ts"})
    if last_bar_ts is not None:
        last_naive = _last_bar_naive_ist_minute(last_bar_ts)
        agg = agg[agg["ts"] > last_naive]
    if agg.empty:
        return 0
    # VPS: timestamptz = start of this minute in Asia/Kolkata
    agg["ts"] = (
        pd.to_datetime(agg["ts"])
        .dt.tz_localize(IST_TZ, ambiguous="infer", nonexistent="shift_forward")
    )
    agg["volume"] = agg["volume"].fillna(0).astype("int64")
    upsert_sql = """
        INSERT INTO ohlc_1min (symbol, ts, open, high, low, close, volume, oi, delta, iv, gamma, theta, vega)
        VALUES (%s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, ts) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
            volume = EXCLUDED.volume, oi = EXCLUDED.oi, delta = EXCLUDED.delta, iv = EXCLUDED.iv,
            gamma = EXCLUDED.gamma, theta = EXCLUDED.theta, vega = EXCLUDED.vega
    """
    rows = []
    for r in agg.itertuples(index=False):
        ts_val = r.ts.to_pydatetime() if hasattr(r.ts, "to_pydatetime") else r.ts
        rows.append((
            r.symbol,
            ts_val,
            float(r.open) if pd.notna(r.open) else None,
            float(r.high) if pd.notna(r.high) else None,
            float(r.low) if pd.notna(r.low) else None,
            float(r.close) if pd.notna(r.close) else None,
            int(r.volume) if pd.notna(r.volume) else 0,
            int(r.oi) if pd.notna(r.oi) else None,
            float(r.delta) if pd.notna(r.delta) else None,
            float(r.iv) if pd.notna(r.iv) else None,
            float(r.gamma) if pd.notna(r.gamma) else None,
            float(r.theta) if pd.notna(r.theta) else None,
            float(r.vega) if pd.notna(r.vega) else None,
        ))
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
        help="Skip tick count per date (faster, shows only 1-min row count)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process only first N dates (for testing)",
    )
    args = parser.parse_args()

    conn = None
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

    # Ensure gamma, theta, vega columns exist (migration for existing DBs)
    db.migrate_ohlc_1min_add_greeks(conn)

    if args.rebuild:
        if args.dry_run:
            dates = get_min_max_dates_ist(conn, None)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ltp_ticks WHERE ltp IS NOT NULL")
                cnt = cur.fetchone()[0]
            print(f"[dry-run] Would TRUNCATE ohlc_1min and rebuild from {cnt:,} ticks ({len(dates)} dates).")
            conn.close()
            return 0
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE ohlc_1min")
        conn.commit()
        dates = get_min_max_dates_ist(conn, None)
        if not dates:
            print("No ltp_ticks data.")
            conn.close()
            return 0
        print(f"Rebuilding ohlc_1min. Processing {len(dates)} date(s)...")
        sys.stdout.flush()
        total_out = 0
        total_in = 0
        n_dates = len(dates)
        for i, d in enumerate(dates):
            if args.limit and i >= args.limit:
                break
            print(f"  [{i+1}/{n_dates}] {d} ", end="", flush=True)
            ticks = 0 if args.fast else count_ticks_for_date(conn, d, None)
            n = run_aggregation_for_date(conn, d, None)
            if args.fast:
                print(f": --> {n:,} 1-min rows", flush=True)
            else:
                print(f": {ticks:,} (5s tick) --> {n:,} 1-min rows", flush=True)
            total_in += ticks
            total_out += n
            conn.commit()
        if args.fast:
            print(f"Done. Rebuilt {total_out:,} 1-min rows.")
        else:
            print(f"Done. Converted {total_in:,} ticks -> {total_out:,} 1-min rows.")
    else:
        last_ts = get_last_ohlc_minute(conn)
        dates = get_min_max_dates_ist(conn, last_ts)
        if not dates:
            print("No new ltp_ticks to process. ohlc_1min is up to date.")
            conn.close()
            return 0

        if args.dry_run:
            print(f"[dry-run] Would process {len(dates)} date(s) from {dates[0]} to {dates[-1]}.")
            conn.close()
            return 0

        # Per-date processing: smaller batches, progress output, no long freezes
        total_out = 0
        total_in = 0
        n_dates = len(dates)
        print(f"Processing {n_dates} date(s) from {dates[0]} to {dates[-1]}...")
        sys.stdout.flush()
        for i, d in enumerate(dates):
            if args.limit and i >= args.limit:
                break
            print(f"  [{i+1}/{n_dates}] {d} ", end="", flush=True)
            ticks = 0 if args.fast else count_ticks_for_date(conn, d, last_ts)
            n = run_aggregation_for_date(conn, d, last_ts)
            if args.fast:
                print(f": --> {n:,} 1-min rows", flush=True)
            else:
                print(f": {ticks:,} (5s tick) --> {n:,} 1-min rows", flush=True)
            total_in += ticks
            total_out += n
            conn.commit()  # Commit each date to avoid long transactions
            # After first date, we've passed last_ts so subsequent dates use None
            if last_ts and d >= last_ts.date():
                last_ts = None
        if args.fast:
            print(f"Done. Inserted {total_out:,} 1-min rows.")
        else:
            print(f"Done. Converted {total_in:,} ticks -> {total_out:,} 1-min rows.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
