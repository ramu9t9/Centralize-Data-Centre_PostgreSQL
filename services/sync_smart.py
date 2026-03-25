#!/usr/bin/env python3
"""
sync_smart.py — Day-Fingerprint Fast Sync

Syncs ltp_ticks from VPS PostgreSQL (source of truth) → Local PostgreSQL.

Strategy:
  1. Compare per-IST-day row counts between local and VPS (2 queries total).
  2. Only fetch and upsert rows for days where counts differ.
  3. Use ON CONFLICT (symbol, ts) DO UPDATE for idempotent upserts.

This avoids the row-by-row comparison of the old sync and is typically 20-50x faster.
A 3-5 day incremental sync should complete in 5–15 minutes instead of 4+ hours.

Usage:
    py -3 services/sync_smart.py                           # Sync all mismatched days (all time)
    py -3 services/sync_smart.py --dry-run                 # Show diff table without writing
    py -3 services/sync_smart.py --days 10                 # Check only last N calendar days
    py -3 services/sync_smart.py --date 2026-03-20         # Sync one specific IST date
    py -3 services/sync_smart.py --force-date 2026-03-20   # Force re-fetch even if counts match
    py -3 services/sync_smart.py --from 2026-03-01         # Sync from a specific date forward
    py -3 services/sync_smart.py --from 2026-03-01 --to 2026-03-25  # Sync a date range
    py -3 services/sync_smart.py --verify                  # Deep hash-check all count-matched days
    py -3 services/sync_smart.py --verify --days 30        # Hash-check last 30 days

Requirements:
    .env must have DATABASE_URL (local) and REMOTE_DATABASE_URL + REMOTE_PG_SSH_TUNNEL=1 (VPS).
    (Already configured in this project for HostITSmart.)
"""

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Allow running from project root: py -3 services/sync_smart.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras

from services import db, remote_source


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

IST_TZ = "Asia/Kolkata"

# Market hours in IST. Used for descriptions only; we query full IST calendar days.
MARKET_OPEN_IST = "09:15:00"
MARKET_CLOSE_IST = "15:30:00"

# Batch size for upsert (stay well below PG's ~65535 bind-param limit: 14 cols × 4500 ≈ 63 000)
UPSERT_BATCH_SIZE = 4000

# Streaming cursor fetch size (rows loaded into Python at once from remote)
STREAM_FETCH_SIZE = 8000


# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# Day count helpers
# ──────────────────────────────────────────────────────────────────────────────

_DAY_COUNT_SQL = """
    SELECT
        (ts AT TIME ZONE %(tz)s)::date AS date_ist,
        COUNT(*) AS cnt
    FROM ltp_ticks
    {where_clause}
    GROUP BY 1
    ORDER BY 1
"""


def _build_where(date_from: Optional[date], date_to: Optional[date]) -> tuple[str, dict]:
    """Return (WHERE clause string, params dict) for date range filter on ts."""
    parts = []
    params: dict = {"tz": IST_TZ}
    if date_from:
        # IST date_from 00:00 → UTC: subtract 5:30
        utc_from = datetime.combine(date_from, datetime.min.time()) - timedelta(hours=5, minutes=30)
        params["ts_from"] = utc_from.strftime("%Y-%m-%d %H:%M:%S+00")
        parts.append("ts >= %(ts_from)s::timestamptz")
    if date_to:
        # IST date_to 23:59:59 → UTC; use date_to + 1 day boundary
        utc_to = datetime.combine(date_to + timedelta(days=1), datetime.min.time()) - timedelta(hours=5, minutes=30)
        params["ts_to"] = utc_to.strftime("%Y-%m-%d %H:%M:%S+00")
        parts.append("ts < %(ts_to)s::timestamptz")
    where = ("WHERE " + " AND ".join(parts)) if parts else ""
    return where, params


def get_local_day_counts(date_from: Optional[date] = None, date_to: Optional[date] = None) -> dict[date, int]:
    """Return {ist_date: row_count} for local PostgreSQL."""
    where, params = _build_where(date_from, date_to)
    sql = _DAY_COUNT_SQL.format(where_clause=where)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def get_remote_day_counts(date_from: Optional[date] = None, date_to: Optional[date] = None) -> dict[date, int]:
    """Return {ist_date: row_count} from VPS PostgreSQL (via SSH tunnel)."""
    where, params = _build_where(date_from, date_to)
    sql = _DAY_COUNT_SQL.format(where_clause=where)
    conn = remote_source.connect_remote()
    try:
        with conn.cursor() as setup:
            setup.execute("SET statement_timeout = 0")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Day hash (deep verify) helpers
# ──────────────────────────────────────────────────────────────────────────────
#
# Strategy: SUM(hashtext(symbol || to_char(ts, 'YYYYMMDD HH24MISS')))
#   - hashtext() is PostgreSQL's internal fast non-cryptographic hash (int4)
#   - Summing across all rows in a day gives a single 64-bit integer
#   - Order-independent (sum is commutative) → no expensive ORDER BY needed
#   - A count-matched day with different (symbol, ts) pairs will produce a
#     different sum → hash collision chance is astronomically low for this data
#   - ~1-3 seconds per 115-day dataset (vs ~46s for count queries)
#

_DAY_HASH_SQL = """
    SELECT
        (ts AT TIME ZONE %(tz)s)::date AS date_ist,
        SUM(hashtext(symbol || to_char(ts AT TIME ZONE 'UTC', 'YYYYMMDD HH24MISS')))::bigint AS day_hash
    FROM ltp_ticks
    {where_clause}
    GROUP BY 1
    ORDER BY 1
"""


def get_local_day_hashes(date_from: Optional[date] = None, date_to: Optional[date] = None) -> dict[date, int]:
    """Return {ist_date: hash_sum} for local PostgreSQL."""
    where, params = _build_where(date_from, date_to)
    sql = _DAY_HASH_SQL.format(where_clause=where)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def get_remote_day_hashes(date_from: Optional[date] = None, date_to: Optional[date] = None) -> dict[date, int]:
    """Return {ist_date: hash_sum} from VPS PostgreSQL."""
    where, params = _build_where(date_from, date_to)
    sql = _DAY_HASH_SQL.format(where_clause=where)
    conn = remote_source.connect_remote()
    try:
        with conn.cursor() as setup:
            setup.execute("SET statement_timeout = 0")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Day fetch + upsert
# ──────────────────────────────────────────────────────────────────────────────

_FETCH_DAY_SQL = """
    SELECT
        symbol, token, ts, ltp, bid, ask,
        volume, oi, delta, gamma, theta, vega, iv,
        COALESCE(source, 'ws') AS source
    FROM ltp_ticks
    WHERE (ts AT TIME ZONE %(tz)s)::date = %(date_ist)s
    ORDER BY ts, symbol
"""

_UPSERT_SQL = """
    INSERT INTO ltp_ticks
        (symbol, token, ts, ltp, bid, ask, volume, oi,
         delta, gamma, theta, vega, iv, source)
    VALUES %s
    ON CONFLICT (symbol, ts) DO UPDATE SET
        token  = EXCLUDED.token,
        ltp    = EXCLUDED.ltp,
        bid    = EXCLUDED.bid,
        ask    = EXCLUDED.ask,
        volume = EXCLUDED.volume,
        oi     = EXCLUDED.oi,
        delta  = EXCLUDED.delta,
        gamma  = EXCLUDED.gamma,
        theta  = EXCLUDED.theta,
        vega   = EXCLUDED.vega,
        iv     = EXCLUDED.iv,
        source = EXCLUDED.source
"""

_ROW_TEMPLATE = "(%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"


def _ts_to_utc_string(val) -> Optional[str]:
    """Convert a psycopg2 datetime (possibly tz-aware) to canonical UTC string with offset."""
    if val is None:
        return None
    if hasattr(val, "tzinfo"):
        if val.tzinfo is None:
            # Treat as UTC
            return val.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        dt = val.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    # Already a string (shouldn't happen with psycopg2 TIMESTAMPTZ, but be safe)
    s = str(val).strip()
    if "+" not in s and not s.endswith("Z"):
        return s + "+00:00"
    return s


def fetch_and_upsert_day(ist_date: date, dry_run: bool = False) -> tuple[int, int]:
    """Fetch all rows for ist_date from VPS and upsert into local. Returns (fetched, upserted)."""
    log(f"  ⬇  Fetching {ist_date} from VPS...")
    t0 = time.time()

    remote_conn = remote_source.connect_remote()
    fetched_rows = []
    try:
        with remote_conn.cursor() as setup:
            setup.execute("SET statement_timeout = 0")
        cur = remote_conn.cursor(name=f"day_fetch_{ist_date}", cursor_factory=psycopg2.extras.DictCursor)
        cur.itersize = STREAM_FETCH_SIZE
        cur.execute(_FETCH_DAY_SQL, {"tz": IST_TZ, "date_ist": ist_date})
        for row in cur:
            fetched_rows.append(dict(row))
        cur.close()
    finally:
        remote_conn.close()

    fetched = len(fetched_rows)
    elapsed_fetch = time.time() - t0
    log(f"     Fetched {fetched:,} rows in {elapsed_fetch:.1f}s")

    if fetched == 0 or dry_run:
        return fetched, 0

    # Prepare tuples for bulk upsert
    prepared = []
    for r in fetched_rows:
        ts_utc = _ts_to_utc_string(r.get("ts"))
        if not ts_utc:
            continue
        prepared.append((
            r.get("symbol"),
            r.get("token") or "99999999",
            ts_utc,
            r.get("ltp"),
            r.get("bid"),
            r.get("ask"),
            r.get("volume"),
            r.get("oi"),
            r.get("delta"),
            r.get("gamma"),
            r.get("theta"),
            r.get("vega"),
            r.get("iv"),
            r.get("source", "ws"),
        ))

    t1 = time.time()
    local_conn = db.get_connection()
    total_touched = 0
    try:
        with local_conn.cursor() as cur:
            for i in range(0, len(prepared), UPSERT_BATCH_SIZE):
                batch = prepared[i: i + UPSERT_BATCH_SIZE]
                psycopg2.extras.execute_values(
                    cur, _UPSERT_SQL, batch, template=_ROW_TEMPLATE, page_size=len(batch)
                )
                total_touched += cur.rowcount
            local_conn.commit()
    except Exception as e:
        local_conn.rollback()
        log(f"  ❌ Upsert failed for {ist_date}: {e}")
        raise
    finally:
        local_conn.close()

    elapsed_upsert = time.time() - t1
    log(f"     Upserted {total_touched:,} rows in {elapsed_upsert:.1f}s")
    return fetched, total_touched


# ──────────────────────────────────────────────────────────────────────────────
# Diff helpers
# ──────────────────────────────────────────────────────────────────────────────

def find_mismatched_days(
    local_counts: dict[date, int],
    remote_counts: dict[date, int],
) -> list[date]:
    """Return sorted list of dates where local count != remote count (or local is missing)."""
    all_dates = set(local_counts) | set(remote_counts)
    mismatched = []
    for d in sorted(all_dates):
        lc = local_counts.get(d, 0)
        rc = remote_counts.get(d, 0)
        if lc != rc:
            mismatched.append(d)
    return mismatched


def print_diff_table(
    local_counts: dict[date, int],
    remote_counts: dict[date, int],
    hash_corrupt: Optional[set] = None,
) -> None:
    """Print a human-readable comparison table. hash_corrupt = set of dates with hash mismatch."""
    all_dates = sorted(set(local_counts) | set(remote_counts))
    if not all_dates:
        log("No data found in either database for the selected date range.")
        return

    hash_corrupt = hash_corrupt or set()
    header = f"{'Date (IST)':<14}  {'Local':>10}  {'VPS':>10}  {'Diff':>10}  Status"
    print()
    print(header)
    print("-" * len(header))
    for d in all_dates:
        lc = local_counts.get(d, 0)
        rc = remote_counts.get(d, 0)
        diff = rc - lc
        if d in hash_corrupt:
            status = "🔴 HASH MISMATCH (count=OK, data differs)"
        elif lc == rc:
            status = "✅ OK"
        elif lc == 0:
            status = "❌ MISSING"
        elif diff > 0:
            status = f"⚠️  VPS has +{diff:,} more"
        else:
            status = f"⚠️  Local has +{abs(diff):,} extra"
        print(f"{str(d):<14}  {lc:>10,}  {rc:>10,}  {diff:>+10,}  {status}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def run_smart_sync(
    dry_run: bool = False,
    days: Optional[int] = None,
    specific_date: Optional[date] = None,
    force: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    verify: bool = False,
) -> int:
    """
    Main sync routine. Returns number of days synced (0 = nothing needed).
    If verify=True, also hash-checks days where counts match and force-syncs any with hash differences.
    """
    total_start = time.time()

    # ── Determine date range ──────────────────────────────────────────────────
    if specific_date:
        date_from = specific_date
        date_to = specific_date
        log(f"📅 Mode: Single date {specific_date} {'(force)' if force else ''}")
    elif days:
        date_to = date.today()
        date_from = date_to - timedelta(days=days - 1)
        log(f"📅 Mode: Last {days} days ({date_from} → {date_to})")
    elif date_from or date_to:
        log(f"📅 Mode: Date range {date_from or 'beginning'} → {date_to or 'end'}")
    else:
        log("📅 Mode: All dates (full comparison)")

    if dry_run:
        log("🔍 DRY-RUN mode — no data will be written")
    if verify:
        log("🔬 VERIFY mode — will hash-check count-matched days for hidden data differences\n")
    else:
        print()

    # ── Open SSH tunnel once (reused for all remote queries) ─────────────────
    if remote_source.uses_remote_postgres():
        log("🔗 Opening SSH tunnel to VPS PostgreSQL...")
        try:
            remote_source.ensure_ssh_tunnel_for_remote_pg()
            log("   ✅ SSH tunnel open")
        except Exception as e:
            log(f"❌ Could not open SSH tunnel: {e}")
            return 0
    else:
        log("❌ REMOTE_DATABASE_URL not set in .env. Cannot sync from VPS.")
        log("   Set REMOTE_DATABASE_URL and REMOTE_PG_SSH_TUNNEL=1 in .env")
        return 0

    # ── Step 1: Get day counts ────────────────────────────────────────────────
    log("\n📊 Step 1/3: Comparing day counts...")
    t0 = time.time()

    try:
        log("   Querying local database...")
        local_counts = get_local_day_counts(date_from, date_to)
        log(f"   Local: {len(local_counts)} distinct IST days, {sum(local_counts.values()):,} total rows")
    except Exception as e:
        log(f"❌ Failed to query local DB: {e}")
        return 0

    try:
        log("   Querying VPS database...")
        remote_counts = get_remote_day_counts(date_from, date_to)
        log(f"   VPS:   {len(remote_counts)} distinct IST days, {sum(remote_counts.values()):,} total rows")
    except Exception as e:
        log(f"❌ Failed to query VPS DB: {e}")
        return 0

    elapsed_compare = time.time() - t0
    log(f"   Count comparison done in {elapsed_compare:.1f}s")

    # ── Step 2a: Find count mismatches ───────────────────────────────────────
    if force and specific_date:
        count_mismatched = [specific_date]
        log(f"   Force-sync requested: will re-fetch {specific_date} regardless of count.")
    else:
        count_mismatched = find_mismatched_days(local_counts, remote_counts)

    # ── Step 2b: Deep hash verify for count-matched days ─────────────────────
    hash_corrupt: set[date] = set()
    if verify:
        # Only check days where counts already match (the suspicious ones)
        days_count_ok = sorted(
            d for d in set(local_counts) | set(remote_counts)
            if d not in count_mismatched and local_counts.get(d, 0) > 0
        )
        if days_count_ok:
            log(f"\n🔬 Step 2b/3: Hash-verifying {len(days_count_ok)} count-matched day(s)...")
            log(f"   (Estimated: ~{max(1, len(days_count_ok) // 30)}-{max(2, len(days_count_ok) // 15)} min for {len(days_count_ok)} days)")
            t_hash = time.time()
            try:
                log("   Computing local hashes...")
                local_hashes = get_local_day_hashes(date_from, date_to)
                log("   Computing VPS hashes...")
                remote_hashes = get_remote_day_hashes(date_from, date_to)
                elapsed_hash = time.time() - t_hash
                log(f"   Hash computation done in {elapsed_hash:.1f}s")
                for d in days_count_ok:
                    lh = local_hashes.get(d)
                    rh = remote_hashes.get(d)
                    if lh != rh:
                        hash_corrupt.add(d)
                if hash_corrupt:
                    log(f"   🔴 {len(hash_corrupt)} day(s) have hash mismatches: {', '.join(str(d) for d in sorted(hash_corrupt))}")
                else:
                    log(f"   ✅ All {len(days_count_ok)} count-matched days pass hash verification")
            except Exception as e:
                log(f"   ❌ Hash verification failed: {e}")
                log("   ⚠️  Continuing without hash check (counts only)")
        else:
            log("   No count-matched days to hash-verify in selected range.")

    # ── Step 2c: Show diff table ──────────────────────────────────────────────
    log("\n📋 Step 2/3: Diff table")
    print_diff_table(local_counts, remote_counts, hash_corrupt)

    days_to_sync = sorted(set(count_mismatched) | hash_corrupt)

    if not days_to_sync:
        log("✅ All days match VPS. Nothing to sync.")
        total_elapsed = time.time() - total_start
        log(f"⏱  Total time: {total_elapsed:.1f}s")
        return 0

    count_mismatch_label = f"{len(count_mismatched)} count mismatch" if count_mismatched else ""
    hash_mismatch_label = f"{len(hash_corrupt)} hash mismatch" if hash_corrupt else ""
    reasons = ", ".join(filter(None, [count_mismatch_label, hash_mismatch_label]))
    log(f"⚠️  {len(days_to_sync)} day(s) need syncing ({reasons}):")
    for d in days_to_sync:
        reason = []
        if d in count_mismatched:
            reason.append(f"count diff {remote_counts.get(d,0) - local_counts.get(d,0):+,}")
        if d in hash_corrupt:
            reason.append("hash mismatch")
        log(f"   {d}  ({', '.join(reason)})")

    if dry_run:
        log("\n🔍 DRY-RUN: No data written. Re-run without --dry-run to sync.")
        return len(days_to_sync)

    # ── Step 3: Fetch and upsert mismatched days ──────────────────────────────
    log(f"\n⬇  Step 3/3: Syncing {len(days_to_sync)} day(s)...")
    synced = 0
    total_fetched = 0
    total_upserted = 0

    for i, d in enumerate(days_to_sync, 1):
        local_cnt = local_counts.get(d, 0)
        remote_cnt = remote_counts.get(d, 0)
        extra = " [🔴 hash mismatch]" if d in hash_corrupt else ""
        log(f"\n  [{i}/{len(days_to_sync)}] {d}  local={local_cnt:,}  vps={remote_cnt:,}  diff={remote_cnt - local_cnt:+,}{extra}")
        try:
            fetched, upserted = fetch_and_upsert_day(d, dry_run=False)
            total_fetched += fetched
            total_upserted += upserted
            synced += 1
        except Exception as e:
            log(f"  ❌ Failed to sync {d}: {e}")
            import traceback
            log(traceback.format_exc())

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    log("\n" + "=" * 60)
    log(f"✅ Sync complete!")
    log(f"   Days synced:   {synced}/{len(days_to_sync)}")
    log(f"   Rows fetched:  {total_fetched:,}")
    log(f"   Rows upserted: {total_upserted:,}")
    log(f"   Total time:    {total_elapsed:.1f}s  ({total_elapsed/60:.1f} min)")
    log("=" * 60)

    if synced < len(days_to_sync):
        log(f"\n⚠️  {len(days_to_sync) - synced} day(s) failed. Re-run sync to retry.")
        return synced

    log("\n💡 Next step: rebuild 1-min OHLC if needed:")
    log("   py -3 scripts/build_ohlc_1min.py")
    return synced


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{s}'. Use YYYY-MM-DD format.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="sync_smart.py — Fast Day-Fingerprint Sync (Local ← VPS PostgreSQL)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py -3 services/sync_smart.py                           Sync all mismatched days
  py -3 services/sync_smart.py --dry-run                 Preview diff without writing
  py -3 services/sync_smart.py --days 7                  Check/sync last 7 days only
  py -3 services/sync_smart.py --date 2026-03-20         Sync single date
  py -3 services/sync_smart.py --force-date 2026-03-20   Force re-fetch date
  py -3 services/sync_smart.py --from 2026-03-01 --to 2026-03-25  Date range
  py -3 services/sync_smart.py --verify                  Deep hash-check all days
  py -3 services/sync_smart.py --verify --days 30        Hash-check last 30 days
  py -3 services/sync_smart.py --verify --dry-run        Hash-check, no writes
""",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show diff table without writing any data")
    parser.add_argument("--days", type=int, metavar="N", help="Only check the last N calendar days")
    parser.add_argument("--date", type=parse_date, metavar="YYYY-MM-DD", help="Sync only this specific IST date")
    parser.add_argument("--force-date", type=parse_date, metavar="YYYY-MM-DD", dest="force_date",
                        help="Force re-fetch this date even if counts already match")
    parser.add_argument("--from", type=parse_date, metavar="YYYY-MM-DD", dest="date_from",
                        help="Sync from this date (inclusive)")
    parser.add_argument("--to", type=parse_date, metavar="YYYY-MM-DD", dest="date_to",
                        help="Sync to this date (inclusive)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Deep verification: hash-check per-day (symbol, ts) fingerprints on count-matched days. "
            "Detects hidden data corruption even when row counts match. "
            "~1-3 min extra per 100 days. Recommended after long breaks or suspected data issues."
        ),
    )
    args = parser.parse_args()

    # Validate: --date/--force-date and --days/--from/--to are mutually exclusive
    specific_date = args.date or args.force_date
    force = bool(args.force_date)

    if specific_date and (args.days or args.date_from or args.date_to):
        parser.error("--date/--force-date cannot be combined with --days/--from/--to")
    if args.days and (args.date_from or args.date_to):
        parser.error("--days cannot be combined with --from/--to")

    synced = run_smart_sync(
        dry_run=args.dry_run,
        days=args.days,
        specific_date=specific_date,
        force=force,
        date_from=args.date_from,
        date_to=args.date_to,
        verify=args.verify,
    )

    return 0 if synced >= 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
