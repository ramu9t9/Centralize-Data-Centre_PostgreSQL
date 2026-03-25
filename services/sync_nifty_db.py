#!/usr/bin/env python3
"""
NIFTY Database Incremental Sync Script (PostgreSQL)
Syncs new/missing ltp_ticks into local PostgreSQL from either:
- Remote PostgreSQL when REMOTE_DATABASE_URL (or SYNC_SOURCE_DATABASE_URL) is set, or
- VPS SQLite over SSH (see services/ssh_vps.py: VPS_HOST, VPS_SSH_PORT, SSH_KEY_PATH, VPS_DB_PATH) when that URL is unset.
"""

import os
import sys
import subprocess
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional
import time
import json
from tqdm import tqdm

# Allow importing services.db when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg2

from services import db
from services import remote_source
from services.ssh_vps import ssh_base_argv, ssh_user_host


class RangeFetchStreamedResult(NamedTuple):
    """Range fetch already streamed rows into local DB; callers must not call insert_records again."""

    rows_fetched: int
    rows_upserted: int


def _is_tty():
    """Return True if stderr is a real terminal (tqdm will use single-line update)."""
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


# Configuration (VPS_HOST / VPS_SSH_PORT / SSH_KEY_PATH: services/ssh_vps.py + .env)
VPS_DB_PATH = os.getenv("VPS_DB_PATH", "/opt/nifty-data-collector/nifty_local.db")

# Local paths (PostgreSQL uses DATABASE_URL; these are for backup/log dirs only)
PROJECT_ROOT = Path(__file__).parent.parent
LOCAL_DB_DIR = Path(os.getenv("LOCAL_DB_DIR", str(PROJECT_ROOT / "data")))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(LOCAL_DB_DIR / "backups")))
LOG_FILE = Path(os.getenv("SYNC_LOG_FILE", str(LOCAL_DB_DIR / "sync_log.txt")))

# Pre-fetch COUNT(*) on VPS SQLite often does a full table scan (~10+ min on millions of rows without an index on ts).
# Default: skip count and stream the export immediately. Set SYNC_LTP_FETCH_COUNT=1 for an exact count and tqdm %.
_DO_ENV_TRUE = frozenset({"1", "true", "yes", "on"})
DO_LTP_FETCH_COUNT = os.getenv("SYNC_LTP_FETCH_COUNT", "").strip().lower() in _DO_ENV_TRUE

# Create directories if they don't exist
LOCAL_DB_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Timestamp canonical format: UTC without timezone suffix (YYYY-MM-DDTHH:MM:SS)
TS_CANONICAL_FORMAT = '%Y-%m-%dT%H:%M:%S'


def normalize_ts(ts_str):
    """Normalize timestamp to canonical format (YYYY-MM-DDTHH:MM:SS, UTC, no suffix).
    
    Args:
        ts_str: Timestamp string in various formats (may have +00:00, Z, etc.)
    
    Returns:
        Normalized timestamp string in format YYYY-MM-DDTHH:MM:SS
    """
    if not ts_str:
        return None
    
    ts_clean = ts_str.strip()
    
    # Remove timezone suffixes
    if ts_clean.endswith('Z'):
        ts_clean = ts_clean[:-1]
    elif '+' in ts_clean:
        # Remove timezone offset (e.g., +00:00, +05:30)
        ts_clean = ts_clean.split('+')[0]
    elif ts_clean.count('-') >= 3 and 'T' in ts_clean:
        # Already in format YYYY-MM-DDTHH:MM:SS or similar
        # Extract just the date-time part (first 19 characters)
        if len(ts_clean) >= 19:
            ts_clean = ts_clean[:19]
    
    # Ensure format is exactly YYYY-MM-DDTHH:MM:SS
    if len(ts_clean) == 19 and ts_clean.count(':') == 2 and 'T' in ts_clean:
        return ts_clean
    
    # Try to parse and reformat
    try:
        # Try ISO format first
        if '+' in ts_str or 'Z' in ts_str:
            dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(ts_str)
        
        # Convert to UTC if timezone-aware
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        
        return dt.strftime(TS_CANONICAL_FORMAT)
    except:
        # Fallback: return as-is if parsing fails
        return ts_clean[:19] if len(ts_clean) >= 19 else ts_clean


def log_message(message):
    """Log message to file and console"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}\n"
    # Handle Unicode for Windows console
    try:
        print(message)
    except UnicodeEncodeError:
        # Fallback: encode with errors='replace' for Windows console
        print(message.encode('ascii', errors='replace').decode('ascii'))
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)


def get_local_db_info():
    """Get information about local PostgreSQL database. Returns (max_ts, count, min_ts) as strings/ints."""
    try:
        conn = db.get_connection()
        if not db.table_exists(conn, "ltp_ticks"):
            conn.close()
            return None, None, None
        cur = conn.cursor()
        cur.execute(
            "SELECT to_char(MAX(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS'), COUNT(*), to_char(MIN(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS') FROM ltp_ticks"
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0], row[1], row[2]
        return None, None, None
    except Exception as e:
        log_message(f"❌ Error reading local DB: {e}")
        return None, None, None


def get_vps_db_info():
    """Source DB stats: remote PostgreSQL (REMOTE_DATABASE_URL) or VPS SQLite over SSH."""
    if remote_source.uses_remote_postgres():
        try:
            conn = remote_source.connect_remote()
            try:
                cur = conn.cursor()
                # Match local db.get_connection() so MAX(ts)/MIN(ts) strings are comparable
                # (otherwise remote defaults to UTC display vs local IST → false "local newer").
                cur.execute("SET timezone = %s", (db.POSTGRES_TIMEZONE,))
                cur.execute(
                    "SELECT to_char(MAX(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS'), COUNT(*), "
                    "to_char(MIN(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS') FROM ltp_ticks"
                )
                row = cur.fetchone()
            finally:
                conn.close()
            if not row or row[1] is None:
                return None, None, None
            vps_latest = normalize_ts(row[0]) if row[0] else None
            vps_earliest = normalize_ts(row[2]) if row[2] else None
            return vps_latest, int(row[1]), vps_earliest
        except Exception as e:
            log_message(f"❌ Error connecting to remote PostgreSQL (REMOTE_DATABASE_URL): {e}")
            return None, None, None

    try:
        cmd = ssh_base_argv() + [
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            ssh_user_host(),
            f"sqlite3 {VPS_DB_PATH} \"SELECT MAX(ts), COUNT(*), MIN(ts) FROM ltp_ticks;\"",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # Increased timeout for large datasets
        )

        if result.returncode != 0:
            err = ((result.stderr or "") + (result.stdout or "")).strip()
            log_message(f"❌ SSH/sqlite3 failed: {err or 'unknown error'}")
            if "no such table" in err.lower():
                log_message(
                    "💡 Wrong or empty SQLite file: missing ltp_ticks. "
                    "Set VPS_DB_PATH in .env (try /opt/nifty-data-collector/data/nifty_local.db). "
                    "On the VPS: find /opt -name 'nifty_local.db' 2>/dev/null ; sqlite3 <path> '.tables'"
                )
                log_message(
                    "💡 If HostITSmart stores ticks only in PostgreSQL, set REMOTE_DATABASE_URL in .env instead of SQLite sync."
                )
            return None, None, None

        parts = result.stdout.strip().split("|")
        if len(parts) == 3:
            # CRITICAL FIX: Normalize timestamps to remove timezone suffix
            vps_latest = normalize_ts(parts[0]) if parts[0] else None
            vps_earliest = normalize_ts(parts[2]) if parts[2] else None
            return vps_latest, int(parts[1]), vps_earliest
        return None, None, None

    except subprocess.TimeoutExpired:
        log_message("❌ SSH connection timeout")
        return None, None, None
    except Exception as e:
        log_message(f"❌ Error connecting to VPS: {e}")
        return None, None, None


def backup_local_db():
    """Create backup of local PostgreSQL database (pg_dump). Returns backup path or None."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"nifty_local_backup_{timestamp}.sql"
    url = os.getenv("DATABASE_URL", "")
    if not url:
        log_message("⚠️  DATABASE_URL not set; backup skipped")
        return None
    try:
        result = subprocess.run(
            ["pg_dump", url],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(result.stdout)
            log_message(f"✅ Backup created: {backup_path.name}")
            return backup_path
        log_message(f"❌ Backup failed: {result.stderr[:200] if result.stderr else 'pg_dump failed'}")
        return None
    except FileNotFoundError:
        log_message("⚠️  pg_dump not found; install PostgreSQL client tools for backup")
        return None
    except Exception as e:
        log_message(f"❌ Backup failed: {e}")
        return None


def adjust_sync_timestamp(timestamp_str):
    """Adjust timestamp to start of day (09:15:00 IST = 03:45:00 UTC) if same day"""
    try:
        # Parse timestamp (handle both with and without timezone)
        ts_str_clean = timestamp_str.replace('Z', '').replace('+00:00', '').strip()
        
        # Try parsing as ISO format
        try:
            if '+' in timestamp_str or 'Z' in timestamp_str:
                ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = datetime.strptime(ts_str_clean, "%Y-%m-%dT%H:%M:%S")
                ts = ts.replace(tzinfo=timezone.utc)
        except:
            # Fallback to simple parsing
            ts = datetime.strptime(ts_str_clean.split('.')[0], "%Y-%m-%dT%H:%M:%S")
            ts = ts.replace(tzinfo=timezone.utc)
        
        # Convert to IST (UTC+5:30)
        ist_offset = timedelta(hours=5, minutes=30)
        ist_time = ts.astimezone(timezone(ist_offset))
        
        # Get date part
        date_part = ist_time.date()
        
        # Market start time in IST: 09:15:00
        market_start_time = datetime.strptime("09:15:00", "%H:%M:%S").time()
        
        # If timestamp is on the same day and after market start, use market start
        if ist_time.date() == date_part and ist_time.time() > market_start_time:
            # Create market start datetime in IST
            market_start_ist = datetime.combine(date_part, market_start_time, tzinfo=timezone(ist_offset))
            # Convert to UTC (subtract 5:30)
            market_start_utc = market_start_ist.astimezone(timezone.utc)
            # Format for SQLite (UTC, no timezone suffix)
            adjusted_ts = market_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
            log_message(f"📅 Adjusted sync start to beginning of day: {adjusted_ts} (09:15:00 IST)")
            return adjusted_ts
        
        # Return original in SQLite-compatible format (UTC, no timezone)
        ts_utc = ts.astimezone(timezone.utc) if ts.tzinfo else ts
        return ts_utc.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception as e:
        log_message(f"⚠️  Error adjusting timestamp: {e}, using original")
        # Try to return in SQLite format
        try:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '').replace('+00:00', '').split('.')[0])
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except:
            return timestamp_str


def _pg_ts_to_tick_string(val):
    """Convert remote PG ts (datetime or str) to canonical UTC string for insert_records."""
    if val is None:
        return None
    if hasattr(val, "strftime"):
        dt = val
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime(TS_CANONICAL_FORMAT)
    return normalize_ts(str(val)) or str(val)[:19]


def _trim_row_key(symbol, ts_val):
    """Stable (symbol, instant) key for trim — must not collapse sub-second timestamps.

    Second-only strings caused many distinct (symbol, ts) rows to share one key, so trim
    under-deleted and local count stayed above remote while incremental sync did nothing.
    """
    sym = (symbol or "").strip()
    if ts_val is None:
        return (sym, None)
    if hasattr(ts_val, "timestamp"):
        dt = ts_val
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return (sym, int(dt.timestamp() * 1_000_000))
    s = str(ts_val).strip()
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # SQLite / legacy text often stores IST wall time without offset
            dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        dt = dt.astimezone(timezone.utc)
        return (sym, int(dt.timestamp() * 1_000_000))
    except Exception:
        ts_fallback = normalize_ts(s) or s[:26]
        return (sym, ts_fallback)


def _fetch_incremental_from_remote_pg(sync_timestamp, do_count):
    """Stream ltp_ticks from REMOTE_DATABASE_URL where ts >= sync_timestamp (UTC boundary)."""
    from psycopg2.extras import RealDictCursor

    ts_bound = db.ensure_utc_suffix(normalize_ts(sync_timestamp) or sync_timestamp)
    total_records = None

    if do_count:
        print("📊 Counting rows on remote PostgreSQL...", end=" ", flush=True)
        try:
            conn_c = remote_source.connect_remote()
            try:
                cur = conn_c.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM ltp_ticks WHERE ts >= %s::timestamptz",
                    (ts_bound,),
                )
                total_records = int(cur.fetchone()[0])
            finally:
                conn_c.close()
        except Exception as e:
            print("❌")
            log_message(f"❌ Remote PostgreSQL count failed: {e}")
            return None
        if total_records == 0:
            print("✅")
            log_message("ℹ️  No new records found")
            return []
        print(f"✅ {total_records:,} rows\n")
    else:
        print(
            "📊 Skipping remote row count (set SYNC_LTP_FETCH_COUNT=1 for tqdm percent on PostgreSQL source)."
        )
        print()

    log_message("📥 Streaming from remote PostgreSQL…")
    conn = None
    try:
        conn = remote_source.connect_remote()
        with conn.cursor() as _setup:
            _setup.execute("SET statement_timeout = 0")
    except Exception as e:
        log_message(f"❌ Remote PostgreSQL connection failed: {e}")
        return None

    records = []
    use_tqdm = _is_tty()
    start_time = time.time()
    line_count = 0
    log_interval = max(50000, (total_records // 20) if total_records else 50000)
    last_log_count = 0

    try:
        cur = conn.cursor(name="ltp_incremental_stream", cursor_factory=RealDictCursor)
        cur.itersize = 8000
        cur.execute(
            """
            SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                   delta, gamma, theta, vega, iv, COALESCE(source, 'ws') AS source
            FROM ltp_ticks
            WHERE ts >= %s::timestamptz
            ORDER BY ts
            """,
            (ts_bound,),
        )
        with tqdm(
            total=total_records,
            desc="Downloading",
            unit="records",
            miniters=max(1, total_records // 100) if total_records else 1000,
            disable=not use_tqdm,
            ncols=100,
            mininterval=0.5,
            file=sys.stderr,
        ) as pbar:
            for row in cur:
                rec = dict(row)
                rec["ts"] = _pg_ts_to_tick_string(rec.get("ts"))
                if not rec.get("ts"):
                    continue
                records.append(rec)
                line_count += 1
                pbar.update(1)
                if use_tqdm and line_count % 2000 == 0:
                    elapsed = time.time() - start_time
                    if elapsed > 0:
                        pbar.set_postfix({"speed": f"{line_count / elapsed:.0f} rec/s"})
                elif not use_tqdm and line_count - last_log_count >= log_interval:
                    elapsed = time.time() - start_time
                    speed = line_count / elapsed if elapsed > 0 else 0
                    if total_records:
                        pct = 100 * line_count / total_records
                        log_message(
                            f"   Downloading: {line_count:,} / {total_records:,} ({pct:.1f}%) - {speed:.0f} rec/s"
                        )
                    else:
                        log_message(f"   Downloading: {line_count:,} rows - {speed:.0f} rec/s")
                    last_log_count = line_count
        cur.close()
    except Exception as e:
        log_message(f"❌ Error streaming from remote PostgreSQL: {e}")
        import traceback

        log_message(traceback.format_exc())
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    elapsed = time.time() - start_time
    if elapsed > 0 and line_count > 0:
        log_message(f"📊 Download speed: {line_count / elapsed:.0f} records/second")
    print()
    log_message(f"✅ Downloaded {len(records)} new records")
    return records


def _remote_pg_conn_for_range_fetch():
    """New connection with session limits disabled (long chunked range reads)."""
    conn = remote_source.connect_remote()
    with conn.cursor() as c:
        c.execute("SET statement_timeout = 0")
        try:
            c.execute("SET idle_in_transaction_session_timeout = 0")
        except Exception:
            pass
    return conn


def _fetch_pg_rows_by_range(
    start_normalized,
    end_normalized,
    chunk_seconds,
    on_chunk: Optional[Callable[[List[dict]], None]] = None,
):
    """Fetch ltp_ticks from remote PostgreSQL for [start, end] in time chunks.

    If on_chunk is set, each successful chunk is passed to on_chunk(rows) and rows are not
    retained (avoids MemoryError on multi-million-row repairs). Returns total row count (int)
    or None on failure. If on_chunk is None, returns the full list of dicts (small ranges only).
    """
    from psycopg2.extras import RealDictCursor
    from datetime import datetime, timedelta

    max_attempts = int(os.getenv("REMOTE_PG_CHUNK_RETRIES", "8").strip() or "8")
    max_attempts = max(1, min(max_attempts, 30))

    start_dt = datetime.fromisoformat(start_normalized)
    end_dt = datetime.fromisoformat(end_normalized)
    total_seconds = (end_dt - start_dt).total_seconds()
    num_chunks = int(total_seconds / chunk_seconds) + 1
    log_message(f"📥 Fetching remote PostgreSQL for range {start_normalized} to {end_normalized}")
    stream_note = "streaming to disk per chunk" if on_chunk else "accumulating in memory"
    log_message(
        f"   Chunks (~{num_chunks}) of {chunk_seconds}s, {stream_note} "
        f"(up to {max_attempts} retries/chunk on disconnect)"
    )

    all_records: List[dict] = []
    total_streamed = 0
    conn = None

    def _close_conn(c):
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    try:
        current_dt = start_dt
        chunk_idx = 0
        while current_dt < end_dt:
            chunk_idx += 1
            chunk_end_dt = min(current_dt + timedelta(seconds=chunk_seconds), end_dt)
            chunk_start = current_dt.strftime("%Y-%m-%dT%H:%M:%S")
            chunk_end = chunk_end_dt.strftime("%Y-%m-%dT%H:%M:%S")
            sb = db.ensure_utc_suffix(chunk_start)
            eb = db.ensure_utc_suffix(chunk_end)
            log_message(f"   Chunk {chunk_idx}: {chunk_start} to {chunk_end}")

            n = 0
            attempt = 0
            while attempt < max_attempts:
                attempt += 1
                chunk_rows = []
                try:
                    if conn is None or conn.closed:
                        conn = _remote_pg_conn_for_range_fetch()
                    cur = conn.cursor(cursor_factory=RealDictCursor)
                    cur.execute(
                        """
                        SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                               delta, gamma, theta, vega, iv, COALESCE(source, 'ws') AS source
                        FROM ltp_ticks
                        WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
                        ORDER BY ts, symbol
                        """,
                        (sb, eb),
                    )
                    for row in cur:
                        rec = dict(row)
                        rec["ts"] = _pg_ts_to_tick_string(rec.get("ts"))
                        chunk_rows.append(rec)
                    cur.close()
                    n = len(chunk_rows)
                    if on_chunk is not None:
                        if chunk_rows:
                            on_chunk(chunk_rows)
                        total_streamed += n
                    else:
                        all_records.extend(chunk_rows)
                    break
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    _close_conn(conn)
                    conn = None
                    log_message(
                        f"   ⚠️  Chunk {chunk_idx} attempt {attempt}/{max_attempts}: {e}"
                    )
                    if attempt >= max_attempts:
                        log_message(f"❌ Error in remote PostgreSQL range fetch (chunk {chunk_idx}): {e}")
                        import traceback

                        log_message(traceback.format_exc())
                        return None
                    if remote_source.ssh_tunnel_enabled():
                        if attempt >= 2:
                            log_message("   🔄 Resetting SSH tunnel before retry…")
                            remote_source.reset_ssh_tunnel()
                    time.sleep(min(60, 2**attempt))
                except Exception as e:
                    _close_conn(conn)
                    conn = None
                    log_message(f"❌ Error in remote PostgreSQL range fetch: {e}")
                    import traceback

                    log_message(traceback.format_exc())
                    return None

            log_message(f"   ✅ Chunk {chunk_idx}: {n} records")
            current_dt = chunk_end_dt
    finally:
        _close_conn(conn)

    if on_chunk is not None:
        log_message(f"✅ Range fetch complete: {total_streamed:,} records streamed (not held in RAM)")
        return total_streamed
    log_message(f"✅ Range fetch complete: {len(all_records)} total records")
    return all_records


def _fetch_pg_ticks_for_timestamps(normalized_ts_list):
    """All ltp_ticks rows at exact timestamps (remote PostgreSQL)."""
    from psycopg2.extras import RealDictCursor

    if not normalized_ts_list:
        return []
    ts_bounds = [db.ensure_utc_suffix(t) for t in normalized_ts_list]
    conn = None
    try:
        conn = remote_source.connect_remote()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                   delta, gamma, theta, vega, iv, COALESCE(source, 'ws') AS source
            FROM ltp_ticks
            WHERE ts = ANY(%s::timestamptz[])
            ORDER BY ts, symbol
            """,
            (ts_bounds,),
        )
        out = []
        for row in cur:
            rec = dict(row)
            rec["ts"] = _pg_ts_to_tick_string(rec.get("ts"))
            out.append(rec)
        return out
    except Exception as e:
        log_message(f"❌ Remote PostgreSQL batch timestamp fetch failed: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def fetch_incremental_data(from_timestamp, use_overlap=True, overlap_minutes=15, fetch_from_start_of_day=False):
    """Fetch records from VPS (incremental) using Python script on VPS
    
    Args:
        from_timestamp: Local latest timestamp
        use_overlap: If True, fetch from (from_timestamp - overlap_minutes) to ensure no missing records
        overlap_minutes: Minutes to go back for overlap (default 15)
        fetch_from_start_of_day: If True, fetch from start of day (for count mismatch scenarios)
    """
    # CRITICAL FIX: Normalize from_timestamp first to handle +00:00 suffix
    from_timestamp = normalize_ts(from_timestamp) or from_timestamp
    
    # FIX: When counts differ but timestamps match, fetch from start of day
    if fetch_from_start_of_day:
        # Fetch from start of the day to catch all missing records
        try:
            from datetime import datetime, timedelta, timezone
            ts_dt = datetime.fromisoformat(from_timestamp.replace('Z', '+00:00'))
            # Convert to IST to get the date
            ist_offset = timedelta(hours=5, minutes=30)
            ist_time = ts_dt.astimezone(timezone(ist_offset))
            date_part = ist_time.date()
            
            # Market start time in IST: 09:15:00
            market_start_time = datetime.strptime("09:15:00", "%H:%M:%S").time()
            market_start_ist = datetime.combine(date_part, market_start_time, tzinfo=timezone(ist_offset))
            # Convert to UTC
            market_start_utc = market_start_ist.astimezone(timezone.utc)
            sync_timestamp = market_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
            log_message(f"📥 Fetching records from VPS from start of day (09:15 IST = {sync_timestamp} UTC) to catch all missing records...")
        except Exception as e:
            # Fallback if parsing fails
            sync_timestamp = adjust_sync_timestamp(from_timestamp)
            log_message(f"📥 Fetching new records from VPS (after {sync_timestamp})...")
    elif use_overlap:
        # Parse timestamp and subtract overlap
        try:
            from datetime import datetime, timedelta, timezone
            ts_dt = datetime.fromisoformat(from_timestamp.replace('Z', '+00:00'))
            overlap_start = ts_dt - timedelta(minutes=overlap_minutes)
            sync_timestamp = overlap_start.strftime("%Y-%m-%dT%H:%M:%S")
            log_message(f"📥 Fetching records from VPS with {overlap_minutes}-minute overlap (from {sync_timestamp} to latest)...")
        except:
            # Fallback if parsing fails
            sync_timestamp = adjust_sync_timestamp(from_timestamp)
            log_message(f"📥 Fetching new records from VPS (after {sync_timestamp})...")
    else:
        # Adjust timestamp to start of day if needed
        sync_timestamp = adjust_sync_timestamp(from_timestamp)
        log_message(f"📥 Fetching new records from VPS (after {sync_timestamp})...")

    if remote_source.uses_remote_postgres():
        return _fetch_incremental_from_remote_pg(sync_timestamp, DO_LTP_FETCH_COUNT)

    # Optional COUNT(*) — without an index on ltp_ticks(ts) this can scan the whole DB and appear "stuck" for 10–30+ min.
    total_records = None
    if DO_LTP_FETCH_COUNT:
        print("📊 Checking how many records to fetch...", end=" ", flush=True)
        count_query = f"SELECT COUNT(*) FROM ltp_ticks WHERE ts >= '{sync_timestamp}'"
        count_cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "ServerAliveInterval=60",
            "-o", "ServerAliveCountMax=3",
            ssh_user_host(),
            f"sqlite3 {VPS_DB_PATH} \"{count_query}\""
        ]

        max_retries = 3
        retry_count = 0
        count_result = None

        while retry_count < max_retries:
            try:
                count_result = subprocess.run(
                    count_cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600  # Large SQLite tables may need a long full scan
                )

                if count_result.returncode == 0:
                    try:
                        total_records = int(count_result.stdout.strip())
                        break
                    except ValueError:
                        pass

                retry_count += 1
                if retry_count < max_retries:
                    log_message(f"⚠️  Count query failed, retrying ({retry_count}/{max_retries})...")
                    time.sleep(2)
                else:
                    log_message(f"❌ Count query failed after {max_retries} attempts")
                    log_message(f"   Error: {count_result.stderr if count_result else ''}")
                    return None

            except subprocess.TimeoutExpired:
                retry_count += 1
                if retry_count < max_retries:
                    log_message(f"⚠️  Count query timeout, retrying ({retry_count}/{max_retries})...")
                    time.sleep(2)
                else:
                    log_message("❌ Count query timeout after 3 attempts (1 hour each)")
                    log_message("💡 On VPS: CREATE INDEX IF NOT EXISTS idx_ltp_ticks_ts ON ltp_ticks(ts); — or unset SYNC_LTP_FETCH_COUNT and skip count")
                    return None

        if total_records == 0:
            print("✅")
            log_message("ℹ️  No new records found")
            return []

        print(f"✅ Found {total_records:,} records\n")
    else:
        skip_msg = (
            "📊 Skipping VPS row count (avoids slow SQLite full scan). "
            "Streaming export… set SYNC_LTP_FETCH_COUNT=1 if you need tqdm with percent."
        )
        print(skip_msg)
        log_message(skip_msg)
        print()
    
    # Create Python export script on VPS
    # CRITICAL FIX: Use LIKE pattern to handle timezone suffix, and normalize timestamps in output
    export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
sync_ts = "{sync_timestamp}"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Use LIKE pattern to match timestamps with or without timezone suffix
query = """
SELECT symbol, token, ts, ltp, bid, ask, volume, oi, 
       delta, gamma, theta, vega, iv, COALESCE(source, 'ws') as source
FROM ltp_ticks 
WHERE ts >= ? OR ts LIKE ?
ORDER BY ts
"""

cursor.execute(query, (sync_ts, sync_ts + '%'))
for row in cursor:
    record = dict(row)
    # CRITICAL FIX: Normalize timestamp before output (remove +00:00 suffix)
    if 'ts' in record and record['ts']:
        ts = record['ts']
        # Remove timezone suffix
        if '+' in ts:
            record['ts'] = ts.split('+')[0]
        elif ts.endswith('Z'):
            record['ts'] = ts[:-1]
    print(json.dumps(record))

conn.close()
'''
    
    # Write script to VPS and execute
    print("📥 Downloading records from VPS (this may take a while for large datasets)...")
    print("   Please wait, downloading in progress...\n")
    
    try:
        # Create temp script on VPS
        temp_script = f"/tmp/export_data_{int(time.time())}.py"
        create_script_cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=10",
            "-o", "TCPKeepAlive=yes",
            ssh_user_host(),
            f"cat > {temp_script} << 'EOFSCRIPT'\n{export_script}\nEOFSCRIPT"
        ]
        
        try:
            subprocess.run(create_script_cmd, capture_output=True, timeout=180, check=True)
        except subprocess.TimeoutExpired:
            log_message("❌ Timeout creating script on VPS")
            return None
        except subprocess.CalledProcessError as e:
            log_message(f"❌ Failed to create script on VPS: {e}")
            return None
        
        # Execute script and get JSON output
        # Use longer keep-alive settings for large downloads
        cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "ServerAliveInterval=30",  # Send keep-alive every 30 seconds
            "-o", "ServerAliveCountMax=20",  # Allow 20 missed keep-alives (10 minutes)
            "-o", "TCPKeepAlive=yes",  # Enable TCP keep-alive
            ssh_user_host(),
            f"python3 {temp_script} && rm {temp_script}"
        ]
        
        # Run command and capture output with progress
        start_time = time.time()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=16384  # Increased buffer size for better performance
        )
        
        records = []
        line_count = 0
        chunk = []
        chunk_size = 1000
        last_activity_time = time.time()
        max_idle_time = 300  # 5 minutes max idle time
        use_tqdm = _is_tty()
        log_interval = max(50000, (total_records // 20) if total_records else 50000)
        last_log_count = 0

        # tqdm shows single-line progress only when stderr is a real terminal (run from terminal, not IDE output panel)
        with tqdm(
            total=total_records,
            desc="Downloading",
            unit="records",
            miniters=max(1, total_records // 100) if total_records else 1000,
            disable=not use_tqdm,
            ncols=100,
            mininterval=0.5,
            file=sys.stderr,
        ) as pbar:
            while True:
                # Check for timeout (no data for too long)
                if time.time() - last_activity_time > max_idle_time:
                    process.terminate()
                    log_message(f"❌ Download timeout: No data received for {max_idle_time} seconds")
                    return None

                # Use select or non-blocking read if available, otherwise use timeout
                line = process.stdout.readline()
                if not line:
                    # Check if process is still running
                    if process.poll() is not None:
                        # Process ended, process remaining chunk
                        if chunk:
                            records.extend(chunk)
                            pbar.update(len(chunk))
                        break
                    # Process still running but no data yet, wait a bit
                    time.sleep(0.1)
                    continue

                last_activity_time = time.time()  # Update activity time
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        # CRITICAL FIX: Ensure timestamp is normalized (should already be done in VPS script, but double-check)
                        if 'ts' in record and record['ts']:
                            record['ts'] = normalize_ts(record['ts']) or record['ts']
                        chunk.append(record)
                        line_count += 1

                        # Update progress in chunks
                        if len(chunk) >= chunk_size:
                            records.extend(chunk)
                            pbar.update(len(chunk))
                            chunk = []

                            # Update speed (tqdm) or periodic log (when not TTY)
                            if use_tqdm:
                                elapsed = time.time() - start_time
                                if elapsed > 0:
                                    speed = line_count / elapsed
                                    pbar.set_postfix({"speed": f"{speed:.0f} rec/s"})
                            elif line_count - last_log_count >= log_interval:
                                elapsed = time.time() - start_time
                                speed = line_count / elapsed if elapsed > 0 else 0
                                if total_records:
                                    pct = 100 * line_count / total_records
                                    log_message(
                                        f"   Downloading: {line_count:,} / {total_records:,} ({pct:.1f}%) - {speed:.0f} rec/s"
                                    )
                                else:
                                    log_message(f"   Downloading: {line_count:,} rows - {speed:.0f} rec/s")
                                last_log_count = line_count
                    except json.JSONDecodeError:
                        continue
        
        # Wait for process to finish (with timeout)
        try:
            process.wait(timeout=60)  # Wait up to 60 seconds for process to finish
        except subprocess.TimeoutExpired:
            process.kill()
            log_message("❌ Process did not finish in time")
            return None
        
        stderr_output = process.stderr.read()
        
        if process.returncode != 0:
            log_message(f"❌ Fetch failed: {stderr_output}")
            return None
        
        elapsed = time.time() - start_time
        if elapsed > 0 and line_count > 0:
            speed = line_count / elapsed
            log_message(f"📊 Download speed: {speed:.0f} records/second")
        
        print()  # New line after progress bar
        log_message(f"✅ Downloaded {len(records)} new records")
        return records
        
    except subprocess.TimeoutExpired:
        log_message("❌ Fetch timeout - the dataset might be very large")
        log_message("💡 Try running the sync again, or check your internet connection")
        log_message("💡 You can also try syncing in smaller chunks by adjusting the date range")
        return None
    except Exception as e:
        log_message(f"❌ Fetch error: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def insert_records(records):
    """Insert into local PostgreSQL using batched UPSERT (execute_values).

    The previous implementation ran SELECT + INSERT per row (~2 round-trips each) and
    COUNT(*) on the whole table twice per call — unusable beyond small batches.
    """
    if not records:
        return 0

    from psycopg2.extras import execute_values

    # PG limit ~65535 bind params; 14 columns → keep batches ≤ ~4500
    batch_size = int(os.getenv("LTP_UPSERT_BATCH_SIZE", "3000").strip() or "3000")
    batch_size = max(200, min(batch_size, 4500))

    prepared = []
    skipped = 0
    for record in records:
        record_ts = normalize_ts(record.get("ts"))
        if not record_ts:
            skipped += 1
            continue
        ts_for_db = db.ensure_utc_suffix(record_ts)
        prepared.append(
            (
                record.get("symbol"),
                record.get("token") or "99999999",
                ts_for_db,
                record.get("ltp"),
                record.get("bid"),
                record.get("ask"),
                record.get("volume"),
                record.get("oi"),
                record.get("delta"),
                record.get("gamma"),
                record.get("theta"),
                record.get("vega"),
                record.get("iv"),
                record.get("source", "ws"),
            )
        )

    n = len(prepared)
    if n == 0:
        if skipped:
            log_message(f"⚠️  Skipped {skipped} records (invalid ts)")
        return 0

    log_message(f"💾 Bulk upsert {n:,} rows (batch size {batch_size:,}, ON CONFLICT)…")

    upsert_sql = """
        INSERT INTO ltp_ticks
        (symbol, token, ts, ltp, bid, ask, volume, oi,
         delta, gamma, theta, vega, iv, source)
        VALUES %s
        ON CONFLICT (symbol, ts) DO UPDATE SET
            token = EXCLUDED.token,
            ltp = EXCLUDED.ltp,
            bid = EXCLUDED.bid,
            ask = EXCLUDED.ask,
            volume = EXCLUDED.volume,
            oi = EXCLUDED.oi,
            delta = EXCLUDED.delta,
            gamma = EXCLUDED.gamma,
            theta = EXCLUDED.theta,
            vega = EXCLUDED.vega,
            iv = EXCLUDED.iv,
            source = EXCLUDED.source
    """
    row_template = "(%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

    log_table_count = os.getenv("LTP_UPSERT_LOG_TABLE_COUNT", "").strip().lower() in _DO_ENV_TRUE

    try:
        conn = db.get_connection()
        cur = conn.cursor()

        count_before = None
        if log_table_count:
            cur.execute("SELECT COUNT(*) FROM ltp_ticks")
            count_before = cur.fetchone()[0]

        total_touched = 0
        total_batches = (n + batch_size - 1) // batch_size
        use_tqdm = _is_tty() and total_batches > 1
        rng = range(0, n, batch_size)
        if use_tqdm:
            rng = tqdm(
                rng,
                desc="Upserting",
                unit="batch",
                total=total_batches,
                ncols=100,
                mininterval=0.4,
                file=sys.stderr,
            )

        log_every = max(1, total_batches // 15)
        for bi, i in enumerate(rng):
            batch = prepared[i : i + batch_size]
            try:
                execute_values(
                    cur,
                    upsert_sql,
                    batch,
                    template=row_template,
                    page_size=len(batch),
                )
                total_touched += cur.rowcount
                conn.commit()
            except Exception as e:
                conn.rollback()
                log_message(f"❌ Bulk upsert failed at row offset {i:,}: {e}")
                import traceback

                log_message(traceback.format_exc())
                conn.close()
                return 0

            if not use_tqdm and total_batches > 3 and (bi + 1) % log_every == 0:
                log_message(f"   … committed batch {bi + 1}/{total_batches}")

        count_after = None
        if log_table_count and count_before is not None:
            cur.execute("SELECT COUNT(*) FROM ltp_ticks")
            count_after = cur.fetchone()[0]

        conn.close()

        if use_tqdm:
            print()

        parts = [f"✅ Upserted {n:,} rows", f"PG rowcount sum ≈ {total_touched:,}"]
        if skipped:
            parts.append(f"skipped invalid ts: {skipped:,}")
        log_message(", ".join(parts))
        if log_table_count and count_before is not None and count_after is not None:
            log_message(
                f"   Table count {count_before:,} → {count_after:,} (net {count_after - count_before:,})"
            )

        return n

    except Exception as e:
        log_message(f"❌ Error inserting records: {e}")
        import traceback

        log_message(traceback.format_exc())
        return 0


def trim_local_to_match_vps(progress_callback=None):
    """
    Delete from local any records that don't exist in VPS.
    VPS is the source of truth - local should be a copy of VPS.
    Use when local has more records than VPS (e.g. test data, duplicates).
    """
    log_message("🔄 Trimming local DB to match VPS (VPS is source of truth)...")

    vps_latest, vps_count, _ = get_vps_db_info()
    local_latest, local_count, _ = get_local_db_info()
    
    if vps_count is None or local_count is None:
        log_message("❌ Cannot get DB info (VPS or local)")
        return -1
    
    if int(local_count) <= int(vps_count):
        log_message(f"✅ Local ({local_count:,}) <= VPS ({vps_count:,}) - nothing to trim")
        return 0
    
    extra = int(local_count) - int(vps_count)
    log_message(f"📊 Local: {local_count:,}, VPS: {vps_count:,} → will remove ~{extra:,} extra records")
    
    # Backup first
    backup_result = backup_local_db()
    if not backup_result:
        log_message("❌ Backup failed - aborting trim")
        return -1
    
    vps_keys = set()

    # --- Remote PostgreSQL: stream keys (same idea as SQLite, no 17M-row SSH cat) ---
    if remote_source.uses_remote_postgres():
        log_message("📥 Streaming (symbol, ts) keys from remote PostgreSQL (may take several minutes)...")
        conn_r = None
        try:
            conn_r = remote_source.connect_remote()
            with conn_r.cursor() as c0:
                c0.execute("SET statement_timeout = 0")
            cur_r = conn_r.cursor(name="trim_key_stream")
            cur_r.itersize = 100000
            cur_r.execute("SELECT symbol, ts FROM ltp_ticks")
            line_count = 0
            for symbol, ts in cur_r:
                vps_keys.add(_trim_row_key(symbol, ts))
                line_count += 1
                if progress_callback and line_count % 500000 == 0 and vps_count:
                    progress_callback(
                        f"Loaded {line_count:,} remote keys...",
                        min(70, 10 + (line_count * 60 // max(vps_count, 1))),
                    )
            cur_r.close()
        except Exception as e:
            log_message(f"❌ Failed to stream keys from remote PostgreSQL: {e}")
            import traceback

            log_message(traceback.format_exc())
            return -1
        finally:
            if conn_r is not None:
                try:
                    conn_r.close()
                except Exception:
                    pass
        log_message(f"✅ Loaded {len(vps_keys):,} keys from remote PostgreSQL")
    else:
        # Build set of (symbol, ts) that exist in VPS (SQLite over SSH)
        db_path = VPS_DB_PATH
        vps_script = f'''
import sqlite3
conn = sqlite3.connect("{db_path}")
cursor = conn.cursor()
cursor.execute("SELECT symbol, ts FROM ltp_ticks")
for row in cursor:
    print(row[0] + "\\t" + (row[1] or ""))
conn.close()
'''
    
        temp_script = f"/tmp/trim_keys_{int(time.time())}.py"
        create_cmd = ssh_base_argv() + [
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30",
            ssh_user_host(),
            f"cat > {temp_script} << 'EOFSCRIPT'\n{vps_script}\nEOFSCRIPT"
        ]
        
        try:
            subprocess.run(create_cmd, capture_output=True, timeout=180, check=True)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            log_message(f"❌ Failed to create script on VPS: {e}")
            return -1
        
        exec_cmd = ssh_base_argv() + [
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=60",
            ssh_user_host(),
            f"python3 {temp_script} 2>/dev/null; rm -f {temp_script}"
        ]
        
        log_message("📥 Fetching (symbol, ts) keys from VPS (this may take a few minutes)...")
        process = subprocess.Popen(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=16384)
        
        line_count = 0
        for line in process.stdout:
            line = line.strip()
            if line and "\t" in line:
                parts = line.split("\t", 1)
                symbol, ts = parts[0], parts[1] if len(parts) > 1 else ""
                vps_keys.add(_trim_row_key(symbol, ts))
                line_count += 1
                if progress_callback and line_count % 500000 == 0 and vps_count:
                    progress_callback(f"Loaded {line_count:,} VPS keys...", min(70, 10 + (line_count * 60 // vps_count)))
        
        process.wait(timeout=600)
        if process.returncode != 0:
            stderr = process.stderr.read() if process.stderr else ""
            log_message(f"❌ VPS script failed: {stderr[:200]}")
            return -1
        
        log_message(f"✅ Loaded {len(vps_keys):,} keys from VPS (SQLite)")
    
    # Find local rows whose key is not on VPS
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, ts FROM ltp_ticks")
    
    to_delete = []
    for row in cursor:
        symbol, ts = row[0], row[1]
        key = _trim_row_key(symbol, ts)
        if key not in vps_keys:
            to_delete.append((symbol, ts))
    
    if not to_delete:
        conn.close()
        log_message("✅ No extra records to delete (local already matches VPS)")
        return 0
    
    log_message(f"🗑️ Deleting {len(to_delete):,} local rows not present on VPS...")
    
    from psycopg2.extras import execute_values

    trim_batch = int(os.getenv("LTP_TRIM_DELETE_BATCH", "5000").strip() or "5000")
    trim_batch = max(500, min(trim_batch, 10000))

    deleted = 0
    cursor.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _sync_trim_extra "
        "(symbol text NOT NULL, ts timestamptz NOT NULL)"
    )
    for i in range(0, len(to_delete), trim_batch):
        batch = to_delete[i : i + trim_batch]
        execute_values(
            cursor,
            "INSERT INTO _sync_trim_extra (symbol, ts) VALUES %s",
            batch,
            page_size=len(batch),
        )
        cursor.execute(
            """
            DELETE FROM ltp_ticks t
            USING _sync_trim_extra e
            WHERE t.symbol = e.symbol AND t.ts = e.ts
            """
        )
        deleted += cursor.rowcount
        cursor.execute("TRUNCATE _sync_trim_extra")
        conn.commit()
        if progress_callback and len(to_delete) > trim_batch:
            progress_callback(
                f"Deleted {deleted:,} / {len(to_delete):,}...",
                70 + min(25, int(i * 25 / max(len(to_delete), 1))),
            )
    
    conn.close()
    log_message(f"✅ Trim complete: removed {deleted:,} records. Re-run sync to align counts with VPS.")
    return deleted


def verify_database():
    """Verify local PostgreSQL database is valid"""
    try:
        conn = db.get_connection()
        if not db.table_exists(conn, "ltp_ticks"):
            conn.close()
            return False
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ltp_ticks")
        count = cur.fetchone()[0]
        conn.close()
        if count == 0:
            return False
        log_message(f"✅ Database verified: {count:,} records")
        return True
    except Exception as e:
        log_message(f"❌ Database verification failed: {e}")
        return False


def backfill_volume_data(date_range=None):
    """Backfill volume data for records that have volume = 0 or NULL by fetching from VPS"""
    log_message("🔄 Starting volume data backfill...")
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Find records with missing volume (0 or NULL) excluding NIFTY 50
        if date_range:
            start_date, end_date = date_range
            query = """
                SELECT symbol, ts FROM ltp_ticks 
                WHERE (symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%' OR symbol LIKE '%%FUT%%')
                AND symbol != 'NIFTY 50'
                AND (volume IS NULL OR volume = 0)
                AND ts >= %s::timestamptz AND ts < %s::timestamptz
                ORDER BY ts
            """
            cursor.execute(query, (start_date, end_date))
        else:
            query = """
                SELECT symbol, ts FROM ltp_ticks 
                WHERE (symbol LIKE '%%CE%%' OR symbol LIKE '%%PE%%' OR symbol LIKE '%%FUT%%')
                AND symbol != 'NIFTY 50'
                AND (volume IS NULL OR volume = 0)
                ORDER BY ts
            """
            cursor.execute(query)
        
        records_to_update = cursor.fetchall()
        conn.close()
        
        if not records_to_update:
            log_message("✅ No records need volume backfill")
            return 0
        
        log_message(f"📊 Found {len(records_to_update):,} records with missing volume")
        
        # Get date range for VPS query
        if date_range:
            start_date, end_date = date_range
        else:
            if records_to_update:
                start_date = records_to_update[0][1]
                end_date = records_to_update[-1][1]
                # Add 1 day to end_date to include all records
                from datetime import datetime, timedelta
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00').split('.')[0])
                end_dt = end_dt + timedelta(days=1)
                end_date = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
            else:
                return 0
        
        log_message(f"📥 Fetching volume data from VPS for {start_date} to {end_date}...")
        
        # Fetch volume data from VPS
        export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

query = """
SELECT symbol, ts, volume, oi, ltp, bid, ask, 
       delta, gamma, theta, vega, iv, 
       COALESCE(source, 'ws') as source
FROM ltp_ticks 
WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%' OR symbol LIKE '%FUT%')
AND symbol != 'NIFTY 50'
AND ts >= '{start_date}' AND ts < '{end_date}'
AND volume IS NOT NULL AND volume > 0
ORDER BY ts
"""

cursor.execute(query)
for row in cursor:
    record = dict(row)
    print(json.dumps(record))

conn.close()
'''
        
        # Create temp script on VPS
        temp_script = f"/tmp/backfill_volume_{int(time.time())}.py"
        create_script_cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            ssh_user_host(),
            f"cat > {temp_script} << 'EOFSCRIPT'\n{export_script}\nEOFSCRIPT"
        ]
        
        subprocess.run(create_script_cmd, capture_output=True, timeout=30)
        
        # Execute script
        cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            ssh_user_host(),
            f"python3 {temp_script} && rm {temp_script}"
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=8192
        )
        
        # Update local PostgreSQL
        local_conn = db.get_connection()
        local_cursor = local_conn.cursor()
        
        updated = 0
        fetched = 0
        
        for line in process.stdout:
            line = line.strip()
            if line:
                try:
                    record = json.loads(line)
                    fetched += 1
                    local_cursor.execute("""
                        UPDATE ltp_ticks 
                        SET volume = %s, oi = %s, ltp = %s, bid = %s, ask = %s,
                            delta = %s, gamma = %s, theta = %s, vega = %s, iv = %s, source = %s
                        WHERE symbol = %s AND ts = %s::timestamptz
                    """, (
                        record.get('volume'),
                        record.get('oi'),
                        record.get('ltp'),
                        record.get('bid'),
                        record.get('ask'),
                        record.get('delta'),
                        record.get('gamma'),
                        record.get('theta'),
                        record.get('vega'),
                        record.get('iv'),
                        record.get('source', 'ws'),
                        record.get('symbol'),
                        record.get('ts')
                    ))
                    if local_cursor.rowcount > 0:
                        updated += 1
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    log_message(f"⚠️  Error updating record: {e}")
                    continue
        
        local_conn.commit()
        local_conn.close()
        process.wait()
        
        log_message(f"✅ Fetched {fetched:,} records from VPS, updated {updated:,} records in local DB")
        return updated
        
    except Exception as e:
        log_message(f"❌ Backfill error: {e}")
        import traceback
        log_message(traceback.format_exc())
        return 0


def cleanup_old_backups(keep=5):
    """Keep only the most recent backups"""
    backups = sorted(BACKUP_DIR.glob("nifty_local_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    
    if len(backups) > keep:
        for backup in backups[keep:]:
            try:
                backup.unlink()
                log_message(f"🗑️  Removed old backup: {backup.name}")
            except Exception as e:
                log_message(f"⚠️  Failed to remove backup {backup.name}: {e}")


def download_full_database_from_vps_OLD():
    """Download entire database from VPS (fresh start) - OLD IMPLEMENTATION"""
    print("\n" + "=" * 60)
    log_message("🔄 Starting FULL Database Download from VPS")
    print("=" * 60 + "\n")
    
    # Get VPS database info
    print("📡 Checking VPS database...", end=" ", flush=True)
    vps_latest, vps_count, vps_earliest = get_vps_db_info()
    
    if vps_latest is None:
        print("❌")
        log_message("❌ Cannot connect to VPS. Aborting download.")
        return False
    print("✅")
    log_message(f"📊 VPS Database: {vps_count:,} records")
    log_message(f"   Earliest: {vps_earliest}")
    log_message(f"   Latest: {vps_latest}")
    
    # Ensure PostgreSQL schema and truncate for fresh start
    print("\n🗑️  Truncating local PostgreSQL tables...", end=" ", flush=True)
    try:
        conn = db.get_connection()
        db.init_postgres_schema(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE ltp_ticks")
            cur.execute("TRUNCATE TABLE oi_snapshots")
        conn.commit()
        conn.close()
        print("✅")
        log_message("✅ Local PostgreSQL tables truncated")
    except Exception as e:
        print("❌")
        log_message(f"❌ Failed to prepare local database: {e}")
        return False
    
    # Fetch ALL records from VPS (from earliest to latest)
    log_message(f"📥 Downloading ALL {vps_count:,} records from VPS...")
    log_message(f"   From: {vps_earliest} to {vps_latest}")
    
    # Fetch from earliest timestamp
    records = fetch_incremental_data(vps_earliest, use_overlap=False, fetch_from_start_of_day=False)
    
    if records is None:
        log_message("❌ Failed to fetch data from VPS")
        return False
    
    if len(records) == 0:
        log_message("⚠️  No records fetched from VPS")
        return False
    
    print(f"\n✅ Downloaded {len(records):,} records from VPS")
    print()  # Empty line before insertion progress
    
    # Insert all records using UPSERT
    inserted = insert_records(records)
    
    if inserted > 0:
        # Verify
        print("\n🔍 Verifying database...", end=" ", flush=True)
        if verify_database():
            print("✅")
            
            # Show final stats
            new_latest, new_count, new_earliest = get_local_db_info()
            print("\n" + "=" * 60)
            log_message(f"✅ Full database download completed successfully!")
            log_message(f"   Downloaded: {len(records):,} records")
            log_message(f"   Inserted: {inserted:,} new records")
            log_message(f"   Total records: {new_count:,}")
            log_message(f"   Earliest: {new_earliest}")
            log_message(f"   Latest: {new_latest}")
            if new_count == vps_count:
                log_message(f"   ✅ Count matches VPS exactly!")
            else:
                log_message(f"   ⚠️  Count differs from VPS (Local: {new_count:,}, VPS: {vps_count:,})")
            print("=" * 60 + "\n")
            return True
        else:
            print("❌")
            log_message("❌ Database verification failed after download")
            return False
    else:
        log_message("⚠️  No records were inserted")
        return False


def download_full_database_from_vps():
    """Download entire database from VPS (fresh start) - calls full_db_sync module"""
    try:
        import sys
        from pathlib import Path
        # Add services directory to path if needed
        services_dir = Path(__file__).parent
        if str(services_dir) not in sys.path:
            sys.path.insert(0, str(services_dir))
        
        from full_db_sync import full_refresh_from_vps
        return full_refresh_from_vps()
    except ImportError as e:
        log_message(f"❌ full_db_sync module not found: {e}")
        log_message("   Please ensure services/full_db_sync.py exists")
        return False
    except Exception as e:
        log_message(f"❌ Error in full refresh: {e}")
        import traceback
        log_message(traceback.format_exc())
        return False


# DEPRECATED: Replaced by batch_find_missing_symbols_for_timestamps()
# This function was too slow (1 SSH call per timestamp)
# def find_exact_missing_symbols_for_timestamp(ts):
#     """Find exact missing symbols for a specific timestamp.
#     
#     Compares VPS and Local to find symbols that exist in VPS but not in Local
#     for the given timestamp.
#     
#     Args:
#         ts: Timestamp string (will be normalized)
#     
#     Returns:
#         List of missing symbol strings, or None if error
#     """
#     try:
#         # Normalize timestamp to canonical format
#         ts_normalized = normalize_ts(ts)
#         if not ts_normalized:
#             log_message(f"⚠️  Could not normalize timestamp: {ts}")
#             return None
#         
#         # Get local symbols for this timestamp
#         conn = sqlite3.connect(str(LOCAL_DB_PATH))
#         cursor = conn.cursor()
#         cursor.execute("SELECT DISTINCT symbol FROM ltp_ticks WHERE ts = ? ORDER BY symbol", (ts_normalized,))
#         local_symbols = set(row[0] for row in cursor.fetchall())
#         conn.close()
#         
#         # Get VPS symbols for this timestamp
#         export_script = f'''import sqlite3
# import json
# import sys
# 
# db_path = "{VPS_DB_PATH}"
# ts = "{ts_normalized}"
# 
# conn = sqlite3.connect(db_path)
# cursor = conn.cursor()
# 
# query = "SELECT DISTINCT symbol FROM ltp_ticks WHERE ts = ? ORDER BY symbol"
# cursor.execute(query, (ts,))
# 
# for row in cursor:
#     print(row[0])
# 
# conn.close()
# '''
#         
#         # Create script on VPS
#         temp_script = create_vps_script(export_script, f"get_symbols_{int(time.time())}")
#         if temp_script is None:
#             return None
#         
#         # Execute script
#         exec_cmd = [
#             "ssh",
#             "-i", SSH_KEY_PATH,
#             "-o", "StrictHostKeyChecking=no",
#             "-o", "ConnectTimeout=30",
#             "-o", "ServerAliveInterval=30",
#             "-o", "ServerAliveCountMax=3",
#             ssh_user_host(),
#             f"python3 {temp_script} 2>&1; rm -f {temp_script}"
#         ]
#         
#         result = subprocess.run(
#             exec_cmd,
#             capture_output=True,
#             text=True,
#             timeout=60
#         )
#         
#         if result.returncode != 0:
#             log_message(f"⚠️  Error getting VPS symbols for {ts_normalized}: {result.stderr}")
#             return None
#         
#         # Parse VPS symbols
#         vps_symbols = set()
#         for line in result.stdout.strip().split('\n'):
#             symbol = line.strip()
#             if symbol:
#                 vps_symbols.add(symbol)
#         
#         # Compute missing symbols
#         missing_symbols = sorted(vps_symbols - local_symbols)
#         
#         return missing_symbols
#         
#     except Exception as e:
#         log_message(f"❌ Error finding exact missing symbols for {ts}: {e}")
#         import traceback
#         log_message(traceback.format_exc())
#         return None


def batch_find_missing_symbols_for_timestamps(timestamps, batch_size=100):
    """Find exact missing symbols for multiple timestamps in batches.
    
    This is much more efficient than calling find_exact_missing_symbols_for_timestamp()
    for each timestamp individually, as it reduces SSH overhead.
    
    Args:
        timestamps: List of timestamp strings (will be normalized)
        batch_size: Number of timestamps to process per SSH call (default 100)
    
    Returns:
        Dict mapping timestamp -> list of missing symbols, or None if error
    """
    try:
        if not timestamps:
            return {}
        
        log_message(f"📊 Batch processing {len(timestamps)} timestamps in batches of {batch_size}...")
        
        all_missing_symbols = {}
        
        # Process in batches to avoid overwhelming SSH/VPS
        for batch_idx in range(0, len(timestamps), batch_size):
            batch = timestamps[batch_idx:batch_idx + batch_size]
            
            # Normalize all timestamps in batch
            normalized_batch = []
            ts_map = {}  # Map normalized -> original
            for ts in batch:
                ts_normalized = normalize_ts(ts)
                if ts_normalized:
                    normalized_batch.append(ts_normalized)
                    ts_map[ts_normalized] = ts
            
            if not normalized_batch:
                continue
            
            # Get local symbols for all timestamps in batch
            conn = db.get_connection()
            cursor = conn.cursor()
            placeholders = ','.join(['%s::timestamptz'] * len(normalized_batch))
            cursor.execute(f"""
                SELECT ts, symbol 
                FROM ltp_ticks 
                WHERE ts IN ({placeholders})
                ORDER BY ts, symbol
            """, normalized_batch)
            local_symbols_by_ts = {}
            for row in cursor.fetchall():
                ts, symbol = row
                if ts not in local_symbols_by_ts:
                    local_symbols_by_ts[ts] = set()
                local_symbols_by_ts[ts].add(symbol)
            conn.close()
            
            # Get VPS symbols for all timestamps in batch
            import json
            export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
timestamps = {json.dumps(normalized_batch)}

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Build query for multiple timestamps using LIKE to handle timezone suffix
# VPS stores timestamps as '2026-01-01T03:45:15+00:00' but we query with '2026-01-01T03:45:15'
conditions = []
for ts in timestamps:
    conditions.append(f"ts LIKE '{{ts}}%'")

query = f"SELECT ts, symbol FROM ltp_ticks WHERE {{' OR '.join(conditions)}} ORDER BY ts, symbol"

cursor.execute(query)

# Output as JSON for easy parsing
result = {{}}
for row in cursor:
    # Normalize the ts from VPS (remove timezone suffix for consistency)
    ts_raw = row[0]
    ts_normalized = ts_raw.split('+')[0] if '+' in ts_raw else ts_raw.replace('Z', '').strip()
    symbol = row[1]
    
    if ts_normalized not in result:
        result[ts_normalized] = []
    result[ts_normalized].append(symbol)

print(json.dumps(result))

conn.close()
'''
            
            # Create script on VPS
            temp_script = create_vps_script(export_script, f"batch_symbols_{batch_idx}")
            if temp_script is None:
                log_message(f"⚠️  Error creating script for batch {batch_idx//batch_size + 1}, skipping")
                continue
            
            # Execute script with longer timeout for batch operations
            exec_cmd = ssh_base_argv() + [                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=10",
                ssh_user_host(),
                f"python3 {temp_script} 2>&1; rm -f {temp_script}"
            ]
            
            try:
                result = subprocess.run(
                    exec_cmd,
                    capture_output=True,
                    text=True,
                    timeout=180  # 3 minutes for batch operations
                )
                
                if result.returncode != 0:
                    log_message(f"⚠️  Error getting VPS symbols for batch {batch_idx//batch_size + 1}: {result.stderr}")
                    continue
                
                # Parse VPS symbols
                vps_symbols_by_ts = json.loads(result.stdout.strip())
                
                # DEBUG: Log what VPS returned for first batch
                if batch_idx == 0:
                    log_message(f"🔍 DEBUG: VPS returned data for {len(vps_symbols_by_ts)} timestamps in first batch")
                    log_message(f"🔍 DEBUG: Expected {len(normalized_batch)} timestamps in batch")
                    if len(vps_symbols_by_ts) < len(normalized_batch):
                        missing_in_vps = set(normalized_batch) - set(vps_symbols_by_ts.keys())
                        log_message(f"🔍 DEBUG: {len(missing_in_vps)} timestamps have NO data in VPS response")
                        log_message(f"🔍 DEBUG: Sample missing: {list(missing_in_vps)[:3]}")
                
                # Compute missing symbols for each timestamp
                for ts_normalized in normalized_batch:
                    local_syms = local_symbols_by_ts.get(ts_normalized, set())
                    vps_syms = set(vps_symbols_by_ts.get(ts_normalized, []))
                    
                    # DEBUG: Log for first few timestamps
                    if batch_idx == 0 and normalized_batch.index(ts_normalized) < 3:
                        log_message(f"🔍 DEBUG: ts={ts_normalized}, local={len(local_syms)}, vps={len(vps_syms)}")
                    
                    missing = sorted(vps_syms - local_syms)
                    if missing:
                        # Use original timestamp as key
                        original_ts = ts_map.get(ts_normalized, ts_normalized)
                        all_missing_symbols[original_ts] = missing
                
                # Log progress
                if (batch_idx // batch_size + 1) % 10 == 0:
                    log_message(f"   Processed {min(batch_idx + batch_size, len(timestamps))}/{len(timestamps)} timestamps...")
                    
            except subprocess.TimeoutExpired:
                log_message(f"⚠️  Timeout for batch {batch_idx//batch_size + 1}, skipping")
                continue
            except json.JSONDecodeError as e:
                log_message(f"⚠️  Error parsing VPS response for batch {batch_idx//batch_size + 1}: {e}")
                continue
        
        log_message(f"✅ Batch processing complete: {len(all_missing_symbols)} timestamps have missing symbols")
        return all_missing_symbols
        
    except Exception as e:
        log_message(f"❌ Error in batch symbol detection: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def fetch_rows_for_timestamp_and_symbols(ts, symbols):
    """Fetch rows from VPS for a specific timestamp and list of symbols.
    
    Args:
        ts: Timestamp string (will be normalized)
        symbols: List of symbol strings to fetch
    
    Returns:
        List of record dictionaries, or None if error
    """
    try:
        if not symbols:
            return []
        
        # Normalize timestamp
        ts_normalized = normalize_ts(ts)
        if not ts_normalized:
            log_message(f"⚠️  Could not normalize timestamp: {ts}")
            return None
        
        # Batch symbols to avoid SQLite parameter limits
        batch_size = 500
        all_records = []
        
        for i in range(0, len(symbols), batch_size):
            symbol_batch = symbols[i:i + batch_size]
            
            # BUG FIX 4: Use json.dumps() for safe symbol list serialization
            # Build parameterized query with LIKE for timestamp to handle +00:00 suffix
            placeholders = ','.join(['?'] * len(symbol_batch))
            
            export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
ts = "{ts_normalized}"
symbols = {json.dumps(symbol_batch)}

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

query = """
SELECT symbol, token, ts, ltp, bid, ask, volume, oi, 
       delta, gamma, theta, vega, iv, COALESCE(source, 'ws') as source
FROM ltp_ticks 
WHERE ts LIKE ? AND symbol IN ({placeholders})
ORDER BY symbol
"""

params = [ts + '%'] + symbols

try:
    cursor.execute(query, params)
    for row in cursor:
        record = dict(row)
        # Normalize ts in the record (remove timezone suffix)
        if 'ts' in record and record['ts']:
            record['ts'] = record['ts'].split('+')[0] if '+' in record['ts'] else record['ts'].replace('Z', '').strip()
        print(json.dumps(record))
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)

conn.close()
'''
            
            # Create script on VPS
            temp_script = create_vps_script(export_script, f"fetch_ts_syms_{i}")
            if temp_script is None:
                log_message(f"⚠️  Error creating script for batch {i//batch_size + 1}")
                continue
            
            # Execute script
            exec_cmd = ssh_base_argv() + [                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=10",
                ssh_user_host(),
                f"python3 {temp_script} 2>&1; rm -f {temp_script}"
            ]
            
            process = subprocess.Popen(
                exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=8192
            )
            
            batch_records = []
            for line in process.stdout:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        batch_records.append(record)
                    except json.JSONDecodeError:
                        continue
            
            process.wait()
            
            if process.returncode != 0:
                stderr_output = process.stderr.read() if process.stderr else ""
                log_message(f"⚠️  Error fetching batch {i//batch_size + 1}: {stderr_output}")
            else:
                all_records.extend(batch_records)
        
        return all_records
        
    except Exception as e:
        log_message(f"❌ Error fetching rows for timestamp {ts} and symbols: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def create_vps_script(script_content, script_name):
    """Create a Python script on VPS reliably (avoids heredoc issues on Windows).
    
    Args:
        script_content: Python script content as string
        script_name: Name for the temp script (will be created in /tmp/)
    
    Returns:
        Path to created script, or None if error
    """
    import time as time_module
    import random
    
    # Use random number in addition to timestamp to avoid collisions
    temp_script = f"/tmp/{script_name}_{int(time_module.time())}_{random.randint(1000, 9999)}.py"
    
    # Use Python stdin method (most reliable, works for any script size)
    # This method pipes the script content directly to Python on the remote side
    try:
        # Escape single quotes in script path for shell
        escaped_script = temp_script.replace("'", "'\"'\"'")
        # Use Python to read from stdin and write to file
        python_cmd = f"python3 -c \"import sys; f=open('{escaped_script}','w', encoding='utf-8'); f.write(sys.stdin.read()); f.close(); print('OK')\""
        
        process = subprocess.Popen(
            ssh_base_argv()
            + [
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=60",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-o", "BatchMode=yes",  # Disable password prompts
                ssh_user_host(),
                python_cmd,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=16384  # Larger buffer for faster transfer
        )
        
        # Write script content and wait for completion
        stdout, stderr = process.communicate(input=script_content, timeout=90)  # 90 second timeout
        
        if process.returncode == 0 and 'OK' in stdout:
            return temp_script
        else:
            error_msg = stderr[:500] if stderr else 'No error message'
            log_message(f"⚠️  Error creating script via stdin: {error_msg}")
            if stdout and 'OK' not in stdout:
                log_message(f"   stdout: {stdout[:200]}")
            return None
            
    except subprocess.TimeoutExpired:
        log_message(f"❌ Timeout creating script on VPS (exceeded 90 seconds)")
        try:
            process.kill()
        except:
            pass
        return None
    except Exception as e:
        log_message(f"❌ Error creating VPS script: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def get_bucket_signatures(level, start_ts, end_ts, side='local'):
    """Get bucket signatures (COUNT, MIN, MAX) for a given time level.
    
    Args:
        level: One of 'month', 'day', 'hour', 'minute', 'second'
        start_ts: Start timestamp (YYYY-MM-DDTHH:MM:SS)
        end_ts: End timestamp (YYYY-MM-DDTHH:MM:SS)
        side: 'local' or 'vps'
    
    Returns:
        Dict mapping bucket_key -> {'count': int, 'min_ts': str, 'max_ts': str}
        Returns None on error
    """
    try:
        # BUG FIX 1: Normalize timestamps to canonical format at the start
        start_ts_normalized = normalize_ts(start_ts)
        end_ts_normalized = normalize_ts(end_ts)
        
        if not start_ts_normalized or not end_ts_normalized:
            log_message(f"❌ Could not normalize timestamps: {start_ts}, {end_ts}")
            return None
        
        # Define substr patterns for each level
        level_patterns = {
            'month': (1, 7),      # YYYY-MM
            'day': (1, 10),       # YYYY-MM-DD
            'hour': (1, 13),      # YYYY-MM-DDTHH
            'minute': (1, 16),    # YYYY-MM-DDTHH:MM
            'second': (1, 19)     # YYYY-MM-DDTHH:MM:SS
        }
        
        if level not in level_patterns:
            log_message(f"❌ Invalid level: {level}")
            return None
        
        start_pos, end_pos = level_patterns[level]
        
        # Content checksum for data match (not just count). Use mod() not % so psycopg2
        # does not treat SQL modulo as printf-style placeholders.
        content_expr = (
            "(COALESCE(ltp,0) * 1000000 + mod(COALESCE(volume,0), 1000000) + mod(COALESCE(oi,0), 100000)) "
            "* (length(COALESCE(symbol,'')) * 31 + length(COALESCE(ts::text,'')) * 17 + 1)"
        )
        if side == 'local':
            # Query local PostgreSQL using normalized timestamps
            to_char_fmt = {'month': "to_char(ts, 'YYYY-MM')", 'day': "to_char(ts, 'YYYY-MM-DD')",
                          'hour': "to_char(ts, 'YYYY-MM-DD\"T\"HH24')", 'minute': "to_char(ts, 'YYYY-MM-DD\"T\"HH24:MI')",
                          'second': "to_char(ts, 'YYYY-MM-DD\"T\"HH24:MI:SS')"}[level]
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT 
                    {to_char_fmt} AS bucket_key,
                    COUNT(*) AS c,
                    MIN(ts) AS min_ts,
                    MAX(ts) AS max_ts,
                    mod(SUM(CAST(({content_expr}) AS DOUBLE PRECISION))::numeric, 2147483647::numeric) AS content_sum
                FROM ltp_ticks 
                WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
                GROUP BY bucket_key
                ORDER BY bucket_key
            """, (start_ts_normalized, end_ts_normalized))
            signatures = {}
            for row in cursor.fetchall():
                bucket_key, count, min_ts, max_ts, content_sum = row
                signatures[bucket_key] = {
                    'count': count,
                    'min_ts': normalize_ts(str(min_ts)) if min_ts else None,
                    'max_ts': normalize_ts(str(max_ts)) if max_ts else None,
                    'content_sum': content_sum
                }
            conn.close()
            return signatures
        
        else:  # VPS (remote PostgreSQL or SQLite over SSH)
            if remote_source.uses_remote_postgres():
                to_char_fmt = {
                    "month": "to_char(ts, 'YYYY-MM')",
                    "day": "to_char(ts, 'YYYY-MM-DD')",
                    "hour": "to_char(ts, 'YYYY-MM-DD\"T\"HH24')",
                    "minute": "to_char(ts, 'YYYY-MM-DD\"T\"HH24:MI')",
                    "second": "to_char(ts, 'YYYY-MM-DD\"T\"HH24:MI:SS')",
                }[level]
                conn = None
                try:
                    conn = remote_source.connect_remote()
                    cursor = conn.cursor()
                    cursor.execute("SET timezone = %s", (db.POSTGRES_TIMEZONE,))
                    cursor.execute(
                        f"""
                        SELECT
                            {to_char_fmt} AS bucket_key,
                            COUNT(*) AS c,
                            MIN(ts) AS min_ts,
                            MAX(ts) AS max_ts,
                            mod(SUM(CAST(({content_expr}) AS DOUBLE PRECISION))::numeric, 2147483647::numeric) AS content_sum
                        FROM ltp_ticks
                        WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
                        GROUP BY bucket_key
                        ORDER BY bucket_key
                        """,
                        (start_ts_normalized, end_ts_normalized),
                    )
                    signatures = {}
                    for row in cursor.fetchall():
                        bucket_key, count, min_ts, max_ts, content_sum = row
                        signatures[bucket_key] = {
                            "count": count,
                            "min_ts": normalize_ts(str(min_ts)) if min_ts else None,
                            "max_ts": normalize_ts(str(max_ts)) if max_ts else None,
                            "content_sum": content_sum,
                        }
                    return signatures
                except Exception as e:
                    log_message(f"❌ Error getting bucket signatures from remote PostgreSQL: {e}")
                    import traceback

                    log_message(traceback.format_exc())
                    return None
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass

            # Query VPS database via SSH (SQLite)
            # Use base64 encoding to avoid heredoc issues on Windows
            import base64
            
            content_expr_vps = (
                "(COALESCE(ltp,0) * 1000000 + COALESCE(volume,0) % 1000000 + COALESCE(oi,0) % 100000) "
                "* (length(COALESCE(symbol,'')) * 31 + length(COALESCE(ts,'')) * 17 + 1)"
            )
            export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
start_ts = "{start_ts_normalized}"
end_ts = "{end_ts_normalized}"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

query = """
SELECT 
    substr(ts, {start_pos}, {end_pos - start_pos + 1}) AS bucket_key,
    COUNT(*) AS c,
    MIN(ts) AS min_ts,
    MAX(ts) AS max_ts,
    (SUM(CAST(({content_expr_vps}) AS REAL)) % 2147483647) AS content_sum
FROM ltp_ticks 
WHERE ts >= ? AND ts <= ?
GROUP BY bucket_key
ORDER BY bucket_key
"""

cursor.execute(query, (start_ts, end_ts))
for row in cursor:
    print(f"{{row[0]}}|{{row[1]}}|{{row[2]}}|{{row[3]}}|{{row[4] if len(row)>4 else 0}}")

conn.close()
'''
            
            # Create script on VPS using reliable method
            temp_script = create_vps_script(export_script, f"get_buckets_{level}")
            if temp_script is None:
                return None
            
            # Execute script (separate rm command to avoid errors if script doesn't exist)
            exec_cmd = ssh_base_argv() + [                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                ssh_user_host(),
                f"python3 {temp_script} 2>&1; rm -f {temp_script}"
            ]
            
            try:
                result = subprocess.run(
                    exec_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes timeout
                )
            except subprocess.TimeoutExpired:
                # This can happen on second-level bucket queries over large ranges.
                # Treat as a recoverable condition (caller can fall back to range-based fetch).
                log_message(
                    f"⚠️  Timeout getting VPS {level} signatures for range {start_ts_normalized} → {end_ts_normalized} "
                    f"(exceeded 300s)"
                )
                return None
            
            if result.returncode != 0:
                # Check if error is just from rm command (script might have succeeded)
                stderr_lines = result.stderr.strip().split('\n') if result.stderr else []
                stdout_lines = result.stdout.strip().split('\n') if result.stdout else []
                
                # If we have output, script probably ran successfully (rm error is harmless)
                if stdout_lines and any('|' in line for line in stdout_lines):
                    # Script succeeded, ignore rm errors
                    pass
                else:
                    log_message(f"❌ Error getting VPS {level} signatures: {result.stderr}")
                    return None
            
            # Parse VPS signatures and normalize timestamps
            signatures = {}
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    parts = line.strip().split('|')
                    if len(parts) >= 4:
                        bucket_key, count, min_ts, max_ts = parts[0], parts[1], parts[2], parts[3]
                        content_sum = int(parts[4]) if len(parts) > 4 and parts[4] else None
                        signatures[bucket_key] = {
                            'count': int(count),
                            'min_ts': normalize_ts(min_ts) if min_ts else None,
                            'max_ts': normalize_ts(max_ts) if max_ts else None,
                            'content_sum': content_sum
                        }
            
            return signatures
            
    except Exception as e:
        log_message(f"❌ Error getting {level} signatures from {side}: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def reconcile_range(start_ts, end_ts, progress_callback=None):
    """Hierarchical reconciliation: Month → Day → Hour → Minute → Second.
    
    Algorithm (following the example pattern):
    1. Month level: Compare COUNT(*) per month (YYYY-MM). Skip matching months.
    2. Day level: For mismatched months, compare COUNT(*) per day (YYYY-MM-DD). Skip matching days.
    3. Hour level: For mismatched days, compare COUNT(*) per hour (YYYY-MM-DDTHH). Skip matching hours.
    4. Minute level: For mismatched hours, compare COUNT(*) per minute (YYYY-MM-DDTHH:MM). Skip matching minutes.
    5. Second level: For mismatched minutes, compare COUNT(*) per exact timestamp (YYYY-MM-DDTHH:MM:SS).
       If VPS count > Local count, add that timestamp to broken_timestamps.
    
    At each level, we compare three metrics: COUNT(*), MIN(ts), MAX(ts).
    Only buckets where all three metrics match are skipped.
    Mismatched buckets are drilled down to the next level.
    
    Example:
        Month 2025-12: VPS=1,450,000, Local=1,449,980 → Drill into 2025-12
        Day 2025-12-03: VPS=60,500, Local=60,480 → Drill into 2025-12-03
        Hour 11: VPS=8,100, Local=8,090 → Drill into 11:00
        Minute 11:01: VPS=140, Local=135 → Drill into 11:01
        Second 11:01:05: VPS=23, Local=18 → Add '2025-12-03T11:01:05' to broken_timestamps
    
    Args:
        start_ts: Start timestamp (YYYY-MM-DDTHH:MM:SS)
        end_ts: End timestamp (YYYY-MM-DDTHH:MM:SS)
        progress_callback: Optional callback function(message, percent) for progress updates
    
    Returns:
        List of timestamps that need to be fetched, or None if error
    """
    try:
        from datetime import datetime, timedelta, timezone
        
        log_message(f"🔍 Starting hierarchical reconciliation: {start_ts} to {end_ts}")
        
        # Level hierarchy with their time ranges
        levels = [
            ('month', 1, 7),      # YYYY-MM
            ('day', 1, 10),       # YYYY-MM-DD
            ('hour', 1, 13),      # YYYY-MM-DDTHH
            ('minute', 1, 16),    # YYYY-MM-DDTHH:MM
            ('second', 1, 19)     # YYYY-MM-DDTHH:MM:SS
        ]
        
        # Start with month level - process entire range
        mismatched_buckets = [(start_ts, end_ts)]
        broken_timestamps = []
        
        for level_idx, (level, start_pos, end_pos) in enumerate(levels):
            if not mismatched_buckets:
                break  # All buckets match, no gaps
            
            # Report progress
            progress_percent = 10 + (level_idx * 15)  # 10%, 25%, 40%, 55%, 70%
            progress_msg = f"Level {level_idx + 1}/{len(levels)}: Checking {level} buckets ({len(mismatched_buckets)} ranges)..."
            log_message(f"📊 {progress_msg}")
            if progress_callback:
                progress_callback(progress_msg, progress_percent)
            
            next_level_buckets = []
            processed_count = 0
            
            # Batch process for day, hour, and second levels to avoid creating too many scripts
            # Day level: batch if more than 3 day ranges
            # Hour level: batch if more than 10 hour ranges  
            # Second level: batch if more than 50 second ranges
            should_batch = False
            chunk_size = 50
            if level == 'day' and len(mismatched_buckets) > 3:
                should_batch = True
                chunk_size = 7  # Process up to 7 days at once
            elif level == 'hour' and len(mismatched_buckets) > 10:
                should_batch = True
                chunk_size = 24  # Process up to 24 hours at once
            elif level == 'second' and len(mismatched_buckets) > 50:
                should_batch = True
                chunk_size = 50  # Process up to 50 minutes at once
            
            if should_batch:
                # Batch process: get all signatures in one query per database
                log_message(f"   Batching {len(mismatched_buckets)} {level}-level checks to avoid timeouts...")
                
                # Process in chunks to avoid huge queries
                for chunk_idx in range(0, len(mismatched_buckets), chunk_size):
                    chunk = mismatched_buckets[chunk_idx:chunk_idx + chunk_size]
                    
                    # Build combined range for this chunk
                    # Find min start and max end from all ranges in chunk
                    chunk_start_ts = min([r[0] for r in chunk])
                    chunk_end_ts = max([r[1] for r in chunk])
                    
                    # Get signatures for this chunk
                    local_sigs = get_bucket_signatures(level, chunk_start_ts, chunk_end_ts, side='local')
                    vps_sigs = get_bucket_signatures(level, chunk_start_ts, chunk_end_ts, side='vps')
                    
                    if local_sigs is None or vps_sigs is None:
                        log_message(f"⚠️  Failed to get {level} signatures for chunk {chunk_idx//chunk_size + 1}")
                        # IMPORTANT: At second-level, signature queries can time out on VPS for large spans.
                        # Instead of losing coverage, mark the entire chunk range as broken so the
                        # downstream fetch can switch to range-sync and still repair the gap.
                        if level == 'second':
                            start_norm = normalize_ts(chunk_start_ts) or chunk_start_ts
                            end_norm = normalize_ts(chunk_end_ts) or chunk_end_ts
                            broken_timestamps.append(start_norm)
                            broken_timestamps.append(end_norm)
                        continue
                    
                    # Compare signatures for this chunk
                    all_buckets = set(local_sigs.keys()) | set(vps_sigs.keys())
                    
                    for bucket_key in sorted(all_buckets):
                        local_sig = local_sigs.get(bucket_key)
                        vps_sig = vps_sigs.get(bucket_key)
                        
                        local_count = local_sig['count'] if local_sig else 0
                        vps_count = vps_sig['count'] if vps_sig else 0
                        local_min = local_sig['min_ts'] if local_sig else None
                        local_max = local_sig['max_ts'] if local_sig else None
                        vps_min = vps_sig['min_ts'] if vps_sig else None
                        vps_max = vps_sig['max_ts'] if vps_sig else None
                        
                        # Check if signatures match (count + min/max + content for data match)
                        local_sum = local_sig.get('content_sum') if local_sig else None
                        vps_sum = vps_sig.get('content_sum') if vps_sig else None
                        content_match = (local_sum == vps_sum) or (local_sum is None and vps_sum is None)
                        if local_count == vps_count and local_min == vps_min and local_max == vps_max and content_match:
                            continue
                        
                        # Bucket mismatch - need to drill down (count, timestamp, or data differs)
                        if level == 'second':
                            # Leaf level - add for fetch+UPSERT (repairs count and/or data mismatch)
                            broken_timestamps.append(bucket_key)
                        else:
                            # Calculate bucket range for next level drill-down
                            # Use the bucket key itself as the range, because min_ts/max_ts might be in a different timezone
                            # and we don't want to query UTC times as if they were local times.
                            # Example bucket_key for month: "2026-03" -> "2026-03-01T00:00:00" to "2026-03-31T23:59:59"
                            # We can just reconstruct the start and end from the bucket_key.
                            import calendar
                            if level == 'month':
                                y, m = int(bucket_key[:4]), int(bucket_key[5:7])
                                _, last_day = calendar.monthrange(y, m)
                                bucket_start_ts = f"{bucket_key}-01T00:00:00"
                                bucket_end_ts = f"{bucket_key}-{last_day:02d}T23:59:59"
                            elif level == 'day':
                                bucket_start_ts = f"{bucket_key}T00:00:00"
                                bucket_end_ts = f"{bucket_key}T23:59:59"
                            elif level == 'hour':
                                bucket_start_ts = f"{bucket_key}:00:00"
                                bucket_end_ts = f"{bucket_key}:59:59"
                            elif level == 'minute':
                                bucket_start_ts = f"{bucket_key}:00"
                                bucket_end_ts = f"{bucket_key}:59"
                            else:
                                continue

                            next_level_buckets.append((bucket_start_ts, bucket_end_ts))
                            processed_count += 1
            else:
                # Normal processing for non-second levels or small second-level batches
                for bucket_start, bucket_end in mismatched_buckets:
                    # Get signatures for this bucket range
                    local_sigs = get_bucket_signatures(level, bucket_start, bucket_end, side='local')
                    vps_sigs = get_bucket_signatures(level, bucket_start, bucket_end, side='vps')
                    
                    if local_sigs is None or vps_sigs is None:
                        log_message(f"⚠️  Failed to get {level} signatures for {bucket_start} to {bucket_end}")
                        continue
                    
                    # Compare signatures
                    all_buckets = set(local_sigs.keys()) | set(vps_sigs.keys())
                    
                    for bucket_key in sorted(all_buckets):
                        local_sig = local_sigs.get(bucket_key)
                        vps_sig = vps_sigs.get(bucket_key)
                        
                        local_count = local_sig['count'] if local_sig else 0
                        vps_count = vps_sig['count'] if vps_sig else 0
                        local_min = local_sig['min_ts'] if local_sig else None
                        local_max = local_sig['max_ts'] if local_sig else None
                        vps_min = vps_sig['min_ts'] if vps_sig else None
                        vps_max = vps_sig['max_ts'] if vps_sig else None
                        
                        # Check if signatures match (count + min/max + content for data match)
                        local_sum = local_sig.get('content_sum') if local_sig else None
                        vps_sum = vps_sig.get('content_sum') if vps_sig else None
                        content_match = (local_sum == vps_sum) or (local_sum is None and vps_sum is None)
                        if local_count == vps_count and local_min == vps_min and local_max == vps_max and content_match:
                            # Bucket matches perfectly (count + data), skip it
                            continue
                        
                        # Bucket mismatch - need to drill down (count, timestamp, or data differs)
                        if level == 'second':
                            # Leaf level - add to broken list for fetch+UPSERT (repairs count and data)
                            broken_timestamps.append(bucket_key)
                        else:
                            # Calculate bucket range for next level drill-down
                            import calendar
                            if level == 'month':
                                y, m = int(bucket_key[:4]), int(bucket_key[5:7])
                                _, last_day = calendar.monthrange(y, m)
                                bucket_start_ts = f"{bucket_key}-01T00:00:00"
                                bucket_end_ts = f"{bucket_key}-{last_day:02d}T23:59:59"
                            elif level == 'day':
                                bucket_start_ts = f"{bucket_key}T00:00:00"
                                bucket_end_ts = f"{bucket_key}T23:59:59"
                            elif level == 'hour':
                                bucket_start_ts = f"{bucket_key}:00:00"
                                bucket_end_ts = f"{bucket_key}:59:59"
                            elif level == 'minute':
                                bucket_start_ts = f"{bucket_key}:00"
                                bucket_end_ts = f"{bucket_key}:59"
                            else:
                                continue

                            next_level_buckets.append((bucket_start_ts, bucket_end_ts))
                            processed_count += 1
            
            mismatched_buckets = next_level_buckets
            if level != 'second':
                log_message(f"   Found {len(mismatched_buckets)} mismatched {level} buckets to drill into")
        
        log_message(f"📋 Total broken timestamps: {len(broken_timestamps)}")
        return broken_timestamps
        
    except Exception as e:
        log_message(f"❌ Error in hierarchical reconciliation: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def detect_mismatched_minutes(start_date, end_date):
    """STEP 1: Fast bucket comparison - compare counts per minute.
    
    Returns:
        List of (minute, vps_count, local_count) tuples for mismatched minutes
    """
    try:
        from datetime import datetime, timedelta, timezone
        
        # Normalize date format
        if len(start_date) == 10:  # YYYY-MM-DD
            start_date = f"{start_date}T00:00:00"
        if len(end_date) == 10:  # YYYY-MM-DD
            end_date = f"{end_date}T23:59:59"
        
        log_message(f"🔍 Step 1: Comparing minute-level counts for {start_date} to {end_date}")
        
        # Get minute-level counts from local PostgreSQL
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT to_char(ts, 'YYYY-MM-DD"T"HH24:MI') AS minute, COUNT(*) AS c
            FROM ltp_ticks 
            WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
            GROUP BY minute
            ORDER BY minute
        """, (start_date, end_date))
        local_minutes = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        
        log_message(f"📊 Local: {len(local_minutes)} unique minutes")
        
        # Get minute-level counts from VPS database
        export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
start_ts = "{start_date}"
end_ts = "{end_date}"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

query = """
SELECT substr(ts, 1, 16) AS minute, COUNT(*) AS c
FROM ltp_ticks 
WHERE ts >= ? AND ts <= ?
GROUP BY minute
ORDER BY minute
"""

cursor.execute(query, (start_ts, end_ts))
for row in cursor:
    print(f"{{row[0]}}|{{row[1]}}")

conn.close()
'''
        
        # Create temp script on VPS using reliable method
        temp_script = create_vps_script(export_script, "detect_minutes")
        if temp_script is None:
            return None
        
        # Execute script (separate rm command to avoid errors)
        exec_cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            ssh_user_host(),
            f"python3 {temp_script} 2>&1; rm -f {temp_script}"
        ]
        
        result = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=120  # 2 minutes timeout
        )
        
        # Check if we have valid output (ignore rm errors)
        if result.returncode != 0:
            stdout_lines = result.stdout.strip().split('\n') if result.stdout else []
            if not stdout_lines or not any('|' in line for line in stdout_lines):
                log_message(f"❌ Error getting VPS minute counts: {result.stderr}")
                return None
        
        # Parse VPS minute counts
        vps_minutes = {}
        for line in result.stdout.strip().split('\n'):
            if '|' in line:
                minute, count = line.strip().split('|', 1)
                vps_minutes[minute] = int(count)
        
        log_message(f"📊 VPS: {len(vps_minutes)} unique minutes")
        
        # Find mismatched minutes
        all_minutes = set(local_minutes.keys()) | set(vps_minutes.keys())
        mismatched = []
        
        for minute in sorted(all_minutes):
            local_count = local_minutes.get(minute, 0)
            vps_count = vps_minutes.get(minute, 0)
            
            if local_count != vps_count:
                mismatched.append((minute, vps_count, local_count))
        
        log_message(f"📋 Found {len(mismatched)} mismatched minutes (out of {len(all_minutes)} total)")
        
        return mismatched
        
    except Exception as e:
        log_message(f"❌ Error detecting mismatched minutes: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def detect_mismatched_timestamps_for_minute(minute_prefix):
    """STEP 2: Drill down to second-level for a broken minute.
    
    Args:
        minute_prefix: Minute string like '2026-01-02T10:00'
    
    Returns:
        List of timestamps where VPS count > Local count
    """
    try:
        # Calculate minute range
        minute_start = f"{minute_prefix}:00"
        minute_end = f"{minute_prefix}:59"
        
        # Get timestamp-level counts from local PostgreSQL
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ts, COUNT(*) AS c
            FROM ltp_ticks 
            WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
            GROUP BY ts
        """, (minute_start, minute_end))
        local_timestamps = {str(row[0]): row[1] for row in cursor.fetchall()}
        conn.close()
        
        # Get timestamp-level counts from VPS database
        export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
minute_start = "{minute_start}"
minute_end = "{minute_end}"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

query = """
SELECT ts, COUNT(*) AS c
FROM ltp_ticks 
WHERE ts >= ? AND ts <= ?
GROUP BY ts
"""

cursor.execute(query, (minute_start, minute_end))
for row in cursor:
    print(f"{{row[0]}}|{{row[1]}}")

conn.close()
'''
        
        # Create temp script on VPS using reliable method
        temp_script = create_vps_script(export_script, "detect_ts")
        if temp_script is None:
            return []
        
        # Execute script (separate rm command to avoid errors)
        exec_cmd = ssh_base_argv() + [            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            ssh_user_host(),
            f"python3 {temp_script} 2>&1; rm -f {temp_script}"
        ]
        
        result = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Check if we have valid output (ignore rm errors)
        if result.returncode != 0:
            stdout_lines = result.stdout.strip().split('\n') if result.stdout else []
            if not stdout_lines or not any('|' in line for line in stdout_lines):
                log_message(f"⚠️  Error getting VPS timestamp counts for {minute_prefix}: {result.stderr}")
                return []
        
        # Parse VPS timestamp counts
        vps_timestamps = {}
        for line in result.stdout.strip().split('\n'):
            if '|' in line:
                ts, count = line.strip().split('|', 1)
                vps_timestamps[ts] = int(count)
        
        # Find timestamps where VPS has more records
        broken_timestamps = []
        all_timestamps = set(local_timestamps.keys()) | set(vps_timestamps.keys())
        
        for ts in sorted(all_timestamps):
            local_count = local_timestamps.get(ts, 0)
            vps_count = vps_timestamps.get(ts, 0)
            
            if vps_count > local_count:
                broken_timestamps.append(ts)
        
        return broken_timestamps
        
    except Exception as e:
        log_message(f"⚠️  Error detecting timestamps for {minute_prefix}: {e}")
        return []


def detect_specific_gaps(start_date, end_date, progress_callback=None):
    """Detect missing records using hierarchical reconciliation (Month → Day → Hour → Minute → Second).
    
    Args:
        start_date: Start date in format 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
        end_date: End date in format 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
        progress_callback: Optional callback function(message, percent) for progress updates
    
    Returns:
        List of timestamps that need to be fetched, or None if error
    """
    try:
        from datetime import datetime, timedelta, timezone
        
        # Normalize date format
        if len(start_date) == 10:  # YYYY-MM-DD
            start_date = f"{start_date}T00:00:00"
        if len(end_date) == 10:  # YYYY-MM-DD
            end_date = f"{end_date}T23:59:59"
        
        # Use new hierarchical reconciliation
        return reconcile_range(start_date, end_date, progress_callback)
        
    except Exception as e:
        log_message(f"❌ Error detecting gaps: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def fetch_vps_rows_by_range(
    start_ts,
    end_ts,
    chunk_seconds=3600,
    on_chunk: Optional[Callable[[List[dict]], None]] = None,
):
    """Fetch ALL rows from VPS for a time range using efficient range queries.
    
    This is much faster than per-timestamp fetching because:
    - Uses WHERE ts >= ? AND ts <= ? (single range query)
    - No LIKE patterns or OR chains
    - Breaks large ranges into chunks to avoid huge downloads
    
    Args:
        start_ts: Start timestamp (YYYY-MM-DDTHH:MM:SS format)
        end_ts: End timestamp (YYYY-MM-DDTHH:MM:SS format)
        chunk_seconds: Size of each chunk in seconds (default 3600 = 1 hour)
        on_chunk: If set, each chunk list is passed here and discarded (no giant in-memory list).
    
    Returns:
        List of records if on_chunk is None; int row count if on_chunk is set; None on error.
    """
    try:
        from datetime import datetime, timedelta
        import json
        
        # Normalize timestamps
        start_normalized = normalize_ts(start_ts)
        end_normalized = normalize_ts(end_ts)
        
        if not start_normalized or not end_normalized:
            log_message(f"❌ Invalid timestamps: {start_ts}, {end_ts}")
            return None

        if remote_source.uses_remote_postgres():
            return _fetch_pg_rows_by_range(
                start_normalized, end_normalized, chunk_seconds, on_chunk=on_chunk
            )

        # Parse timestamps
        start_dt = datetime.fromisoformat(start_normalized)
        end_dt = datetime.fromisoformat(end_normalized)
        
        # Calculate total duration
        total_seconds = (end_dt - start_dt).total_seconds()
        num_chunks = int(total_seconds / chunk_seconds) + 1
        
        log_message(f"📥 Fetching VPS data for range {start_normalized} to {end_normalized}")
        log_message(f"   Breaking into {num_chunks} chunks of {chunk_seconds}s each")
        
        all_records = []
        total_streamed = 0
        
        # Process in chunks
        current_dt = start_dt
        chunk_idx = 0
        
        while current_dt < end_dt:
            chunk_idx += 1
            chunk_end_dt = min(current_dt + timedelta(seconds=chunk_seconds), end_dt)
            
            chunk_start = current_dt.strftime('%Y-%m-%dT%H:%M:%S')
            chunk_end = chunk_end_dt.strftime('%Y-%m-%dT%H:%M:%S')
            
            log_message(f"   Chunk {chunk_idx}/{num_chunks}: {chunk_start} to {chunk_end}")
            
            # Build VPS query script using range query (no OR chains)
            export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
start_ts = "{chunk_start}"
end_ts = "{chunk_end}"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Use LIKE for both bounds to handle timezone suffix (+00:00)
# This matches: start_ts, start_ts+00:00, start_ts+05:30, etc.
query = """
SELECT symbol, token, ts, ltp, bid, ask, volume, oi, 
       delta, gamma, theta, vega, iv, COALESCE(source, 'ws') as source
FROM ltp_ticks 
WHERE (ts LIKE ? OR ts > ?) AND (ts LIKE ? OR ts < ?)
ORDER BY ts, symbol
"""

cursor.execute(query, [start_ts + '%', start_ts, end_ts + '%', end_ts])

for row in cursor:
    record = dict(row)
    # Normalize ts (remove timezone suffix)
    if 'ts' in record and record['ts']:
        record['ts'] = record['ts'].split('+')[0] if '+' in record['ts'] else record['ts'].replace('Z', '').strip()
    print(json.dumps(record))

conn.close()
'''
            
            # Create script on VPS
            temp_script = create_vps_script(export_script, f"range_fetch_{chunk_idx}")
            if temp_script is None:
                log_message(f"   ⚠️  Error creating script for chunk {chunk_idx}, skipping")
                current_dt = chunk_end_dt
                continue
            
            # Execute script
            exec_cmd = ssh_base_argv() + [                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=10",
                ssh_user_host(),
                f"python3 {temp_script} 2>&1; rm -f {temp_script}"
            ]
            
            try:
                process = subprocess.Popen(
                    exec_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=16384
                )
                
                chunk_records = []
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            chunk_records.append(record)
                        except json.JSONDecodeError:
                            continue
                
                process.wait(timeout=300)  # 5 minutes per chunk
                
                if process.returncode == 0:
                    nch = len(chunk_records)
                    if on_chunk is not None:
                        if chunk_records:
                            on_chunk(chunk_records)
                        total_streamed += nch
                    else:
                        all_records.extend(chunk_records)
                    log_message(f"   ✅ Chunk {chunk_idx}: Fetched {nch} records")
                else:
                    stderr_output = process.stderr.read() if process.stderr else ""
                    log_message(f"   ⚠️  Chunk {chunk_idx} error: {stderr_output[:200]}")
                    
            except subprocess.TimeoutExpired:
                log_message(f"   ⚠️  Chunk {chunk_idx} timeout, skipping")
                try:
                    process.kill()
                except:
                    pass
            except Exception as e:
                log_message(f"   ⚠️  Chunk {chunk_idx} exception: {e}")
            
            current_dt = chunk_end_dt
        
        if on_chunk is not None:
            log_message(f"✅ Range fetch complete: {total_streamed:,} records streamed (SQLite)")
            return total_streamed
        log_message(f"✅ Range fetch complete: {len(all_records)} total records")
        return all_records
        
    except Exception as e:
        log_message(f"❌ Error in fetch_vps_rows_by_range: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def fetch_missing_records_for_date_range(start_date, end_date, broken_timestamps=None):
    """STEP 3: Fetch records by timestamp ranges (not pairs).
    
    Args:
        start_date: Start date in format 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
        end_date: End date in format 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
        broken_timestamps: Optional list of timestamps to fetch. If None, will detect gaps first.
    
    Returns:
        List of records, RangeFetchStreamedResult (remote PG large range — already upserted), or None on error.
    """
    try:
        from datetime import datetime, timedelta, timezone
        
        # Normalize date format
        if len(start_date) == 10:  # YYYY-MM-DD
            start_date = f"{start_date}T00:00:00"
        if len(end_date) == 10:  # YYYY-MM-DD
            end_date = f"{end_date}T23:59:59"
        
        # Detect gaps if not provided
        if broken_timestamps is None:
            log_message("🔍 Detecting missing records...")
            broken_timestamps = detect_specific_gaps(start_date, end_date)
            if broken_timestamps is None:
                log_message("❌ Failed to detect gaps")
                return None
        
        if not broken_timestamps:
            log_message("✅ No missing records found for this date range")
            return []
        
        log_message(f"📥 Fetching missing records for {len(broken_timestamps)} broken timestamps from VPS...")
        
        # Debug: Log first few timestamps to verify format
        if broken_timestamps:
            sample_ts = broken_timestamps[:3]
            log_message(f"🔍 Sample suspicious timestamps: {sample_ts}")
        
        # DECISION LOGIC: Choose strategy based on gap size
        # Calculate time span of broken timestamps
        from datetime import datetime
        
        if len(broken_timestamps) > 0:
            first_ts = normalize_ts(broken_timestamps[0])
            last_ts = normalize_ts(broken_timestamps[-1])
            
            if first_ts and last_ts:
                first_dt = datetime.fromisoformat(first_ts)
                last_dt = datetime.fromisoformat(last_ts)
                gap_seconds = (last_dt - first_dt).total_seconds()
                
                log_message(f"📊 Gap analysis: {len(broken_timestamps)} timestamps spanning {gap_seconds:.0f} seconds")
                
                # Decision: Use range sync for large gaps (> 120 seconds)
                if gap_seconds > 120:
                    log_message(f"🚀 Using RANGE SYNC (gap > 120s): Fetching by time range instead of per-timestamp")
                    log_message(f"   Range: {first_ts} to {last_ts}")
                    
                    # Remote PostgreSQL: stream each hour-chunk to insert_records (avoids MemoryError on millions of rows).
                    if remote_source.uses_remote_postgres():
                        upserted_stream = 0

                        def _range_on_chunk(ch: List[dict]):
                            nonlocal upserted_stream
                            if ch:
                                upserted_stream += insert_records(ch)

                        nread = fetch_vps_rows_by_range(
                            first_ts,
                            last_ts,
                            chunk_seconds=3600,
                            on_chunk=_range_on_chunk,
                        )
                        if nread is None:
                            log_message("❌ Range sync failed")
                            return None
                        return RangeFetchStreamedResult(
                            rows_fetched=nread, rows_upserted=upserted_stream
                        )

                    all_records = fetch_vps_rows_by_range(first_ts, last_ts, chunk_seconds=3600)
                    if all_records is None:
                        log_message("❌ Range sync failed")
                        return None

                    return all_records
                else:
                    log_message(f"🎯 Using EXACT SYNC (gap <= 120s): Fetching per-timestamp for precision")
            else:
                log_message("⚠️  Could not parse timestamps for gap analysis, using exact sync")
        
        # EXACT SYNC: For small gaps or when range sync is not applicable
        # Fetch ALL records for broken timestamps in batches
        log_message(f"📥 Fetching records for {len(broken_timestamps)} timestamps in batches...")
        
        all_records = []
        
        # Batch the timestamps (100 per batch for reliability)
        batch_size = 100
        for batch_idx in range(0, len(broken_timestamps), batch_size):
            batch = broken_timestamps[batch_idx:batch_idx + batch_size]
            
            # Normalize timestamps
            normalized_batch = []
            for ts in batch:
                ts_normalized = normalize_ts(ts)
                if ts_normalized:
                    normalized_batch.append(ts_normalized)
            
            if not normalized_batch:
                continue
            
            log_message(f"   Fetching batch {batch_idx//batch_size + 1}/{(len(broken_timestamps) + batch_size - 1)//batch_size} ({len(normalized_batch)} timestamps)...")

            if remote_source.uses_remote_postgres():
                batch_records = _fetch_pg_ticks_for_timestamps(normalized_batch)
                if batch_records is None:
                    log_message(f"   ⚠️  Batch {batch_idx//batch_size + 1} failed (remote PostgreSQL)")
                    continue
                all_records.extend(batch_records)
                log_message(f"   ✅ Batch {batch_idx//batch_size + 1}: Fetched {len(batch_records)} records")
                continue

            # Fetch ALL records for these timestamps from VPS SQLite in one query (SSH)
            import json
            
            export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"
timestamps = {json.dumps(normalized_batch)}

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Build LIKE conditions for timestamps to handle +00:00 suffix
conditions = []
for ts in timestamps:
    conditions.append(f"ts LIKE '{{ts}}%'")

query = f"""
SELECT symbol, token, ts, ltp, bid, ask, volume, oi, 
       delta, gamma, theta, vega, iv, COALESCE(source, 'ws') as source
FROM ltp_ticks 
WHERE {{' OR '.join(conditions)}}
ORDER BY ts, symbol
"""

cursor.execute(query)
for row in cursor:
    record = dict(row)
    # Normalize ts in the record (remove timezone suffix)
    if 'ts' in record and record['ts']:
        record['ts'] = record['ts'].split('+')[0] if '+' in record['ts'] else record['ts'].replace('Z', '').strip()
    print(json.dumps(record))

conn.close()
'''
            
            # Create script on VPS
            temp_script = create_vps_script(export_script, f"bulk_fetch_{batch_idx}")
            if temp_script is None:
                log_message(f"⚠️  Error creating script for batch {batch_idx//batch_size + 1}, skipping")
                continue
            
            # Execute script
            exec_cmd = ssh_base_argv() + [                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=10",
                ssh_user_host(),
                f"python3 {temp_script} 2>&1; rm -f {temp_script}"
            ]
            
            try:
                process = subprocess.Popen(
                    exec_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=16384
                )
                
                batch_records = []
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            batch_records.append(record)
                        except json.JSONDecodeError:
                            continue
                
                process.wait(timeout=300)  # 5 minutes per batch
                
                if process.returncode == 0:
                    all_records.extend(batch_records)
                    log_message(f"   ✅ Batch {batch_idx//batch_size + 1}: Fetched {len(batch_records)} records")
                else:
                    stderr_output = process.stderr.read() if process.stderr else ""
                    log_message(f"   ⚠️  Batch {batch_idx//batch_size + 1} error: {stderr_output[:200]}")
                    
            except subprocess.TimeoutExpired:
                log_message(f"   ⚠️  Batch {batch_idx//batch_size + 1} timeout, skipping")
                try:
                    process.kill()
                except:
                    pass
                continue
            except Exception as e:
                log_message(f"   ⚠️  Batch {batch_idx//batch_size + 1} exception: {e}")
                continue
        
        log_message(f"✅ Total fetched: {len(all_records)} records from {len(broken_timestamps)} broken timestamps")
        return all_records
        
    except Exception as e:
        log_message(f"❌ Error fetching missing records: {e}")
        import traceback
        log_message(traceback.format_exc())
        return None


def sync_database(force=False):
    """Main incremental sync function"""
    print("\n" + "=" * 60)
    log_message("🔄 Starting NIFTY Database Incremental Sync")
    print("=" * 60 + "\n")
    
    # Check if local PostgreSQL is reachable and has schema
    try:
        conn = db.get_connection()
        if not db.table_exists(conn, "ltp_ticks"):
            conn.close()
            log_message("❌ Local table ltp_ticks not found. Run init or full sync first.")
            return False
        conn.close()
    except Exception as e:
        log_message("❌ Cannot connect to local database!")
        log_message("💡 Set DATABASE_URL (e.g. postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data)")
        log_message(f"   {e}")
        return False
    
    # Get source database info (remote PostgreSQL or VPS SQLite)
    _src_label = (
        "remote PostgreSQL (REMOTE_DATABASE_URL)"
        if remote_source.uses_remote_postgres()
        else "VPS (SSH + SQLite)"
    )
    print(f"📡 Checking source database ({_src_label})...", end=" ", flush=True)
    vps_latest, vps_count, vps_earliest = get_vps_db_info()

    if vps_latest is None:
        print("❌")
        log_message("❌ Cannot connect to sync source. Aborting sync.")
        return False
    print("✅")
    log_message(f"📊 Source database: {vps_count:,} records, Latest: {vps_latest}")
    
    # Get local database info
    print("💾 Checking local database...", end=" ", flush=True)
    local_latest, local_count, local_earliest = get_local_db_info()
    
    if local_count is None:
        print("❌")
        log_message("❌ Cannot read local database. Aborting sync.")
        return False
    if local_latest is None and local_count is not None:
        # Empty table (MAX(ts) is NULL) – do full download from VPS
        print("✅ (empty)")
        log_message("📊 Local Database: 0 records (empty). Starting full sync from VPS...")
        return download_full_database_from_vps()
    print("✅")
    log_message(f"📊 Local Database: {local_count:,} records, Latest: {local_latest}")
    
    # STEP 1: If local has MORE records than VPS, trim first (VPS is source of truth)
    if int(local_count) > int(vps_count):
        extra = int(local_count) - int(vps_count)
        log_message(
            f"✂️ Local has {extra:,} more rows than source — trimming to match "
            f"({'remote PostgreSQL' if remote_source.uses_remote_postgres() else 'VPS SQLite'})…"
        )
        deleted = trim_local_to_match_vps()
        if deleted >= 0:
            log_message(f"✅ Trimmed {deleted:,} extra records")
            local_latest, local_count, local_earliest = get_local_db_info()
            if local_count is None:
                log_message("❌ Failed to re-read local DB after trim")
                return False
        else:
            log_message("❌ Trim failed - aborting sync")
            return False
    
    # Compare timestamps AND counts (FIX: same MAX(ts) doesn't mean fully synced)
    local_norm = normalize_ts(local_latest) or local_latest
    vps_norm = normalize_ts(vps_latest) or vps_latest
    if local_norm == vps_norm:
        if local_count == vps_count:
            # Counts match - verify data match (content checksum) via reconciliation
            from datetime import datetime
            if local_earliest and vps_earliest:
                earliest = min(local_earliest, vps_earliest)
            else:
                earliest = local_earliest or vps_earliest
            if earliest:
                try:
                    earliest_dt = datetime.fromisoformat(earliest.replace('Z', '+00:00'))
                    latest_dt = datetime.fromisoformat(local_latest.replace('Z', '+00:00'))
                    start_date = earliest_dt.strftime("%Y-%m-%dT%H:%M:%S")
                    end_date = latest_dt.strftime("%Y-%m-%dT%H:%M:%S")
                    log_message("🔍 Verifying data match (count + content)...")
                    broken_timestamps = reconcile_range(start_date, end_date)
                    if broken_timestamps and len(broken_timestamps) > 0:
                        log_message(f"📋 Found {len(broken_timestamps)} timestamps with data mismatch - repairing...")
                        records = fetch_missing_records_for_date_range(start_date, end_date, broken_timestamps)
                        if isinstance(records, RangeFetchStreamedResult):
                            backup_local_db()
                            log_message(
                                f"✅ Data repair complete: {records.rows_upserted:,} records upserted "
                                f"({records.rows_fetched:,} fetched, streamed)"
                            )
                            return True
                        if records and len(records) > 0:
                            backup_local_db()
                            inserted = insert_records(records)
                            log_message(f"✅ Data repair complete: {inserted:,} records updated")
                            return True
                    else:
                        print("\n✅ Databases are already in sync (count + data match)!")
                        return True
                except Exception as e:
                    log_message(f"⚠️ Data verification skipped: {e}")
            print("\n✅ Databases are already in sync (timestamp + count match)!")
            return True
        else:
            # Same latest timestamp but counts differ → trim extras OR pull missing rows
            extra_local = local_count - vps_count
            missing_on_local = vps_count - local_count
            print(f"\n⚠️  Same latest timestamp but counts differ!")
            print(f"   Local: {local_count:,} records, source: {vps_count:,} records")
            if extra_local > 0:
                print(f"   Local is ahead by {extra_local:,} rows (extras to trim or key mismatch).")
            elif missing_on_local > 0:
                print(f"   Local is short by {missing_on_local:,} rows (need fetch / repair).")
            
            from datetime import datetime, timedelta, timezone
            
            # Local still larger than source: incremental sync cannot fix (no "negative fetch")
            if extra_local > 0:
                log_message(
                    f"✂️ Local still has {extra_local:,} more rows than source — trimming again "
                    f"(precise symbol+timestamp keys)…"
                )
                deleted = trim_local_to_match_vps()
                if deleted < 0:
                    log_message("❌ Re-trim failed")
                    return False
                local_latest, local_count, local_earliest = get_local_db_info()
                if local_count is None:
                    log_message("❌ Failed to re-read local DB after re-trim")
                    return False
                extra_local = local_count - vps_count
                missing_on_local = vps_count - local_count
                if extra_local > 0:
                    log_message(
                        f"❌ After re-trim local still exceeds source by {extra_local:,}. "
                        "Inspect sample keys or consider TRUNCATE ltp_ticks + full resync."
                    )
                    return False

            if local_count == vps_count:
                print("\n✅ Row counts match source.")
                return True

            missing_on_local = vps_count - local_count
            
            # Get date range from earliest to latest
            if local_earliest and vps_earliest:
                earliest = min(local_earliest, vps_earliest)
            else:
                earliest = local_earliest or vps_earliest
            
            if earliest and missing_on_local > 0:
                # Parse earliest date and check last 7 days for gaps
                try:
                    earliest_dt = datetime.fromisoformat(earliest.replace('Z', '+00:00'))
                    latest_dt = datetime.fromisoformat(local_latest.replace('Z', '+00:00'))
                    
                    if missing_on_local > 100:
                        start_dt = earliest_dt
                        log_message(
                            f"🔍 Using hierarchical reconciliation for {missing_on_local:,} "
                            f"missing-on-local rows (full range: {earliest_dt.strftime('%Y-%m-%d')} to "
                            f"{latest_dt.strftime('%Y-%m-%d')})…"
                        )
                        
                        start_date = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
                        end_date = latest_dt.strftime("%Y-%m-%dT%H:%M:%S")
                        
                        broken_timestamps = reconcile_range(start_date, end_date)
                        
                        if broken_timestamps and len(broken_timestamps) > 0:
                            log_message(f"📋 Found {len(broken_timestamps)} broken timestamps")
                            records = fetch_missing_records_for_date_range(start_date, end_date, broken_timestamps)
                            
                            if records is None:
                                log_message("❌ Failed to fetch missing records, falling back to incremental sync")
                                records = fetch_incremental_data(local_latest, use_overlap=True, overlap_minutes=15, fetch_from_start_of_day=False)
                            elif isinstance(records, RangeFetchStreamedResult):
                                if records.rows_fetched == 0:
                                    print("\n✅ No missing records to sync")
                                    return True
                            elif len(records) == 0:
                                print("\n✅ No missing records to sync")
                                return True
                        else:
                            log_message("📋 No specific gaps detected, using incremental sync")
                            records = fetch_incremental_data(local_latest, use_overlap=True, overlap_minutes=15, fetch_from_start_of_day=False)
                    else:
                        log_message(
                            f"📋 Shortfall {missing_on_local:,} rows on local — incremental sync with overlap"
                        )
                        records = fetch_incremental_data(local_latest, use_overlap=True, overlap_minutes=15, fetch_from_start_of_day=False)
                    
                    # Insert list-based fetch, or verify after streamed range repair
                    if records is None:
                        print("\n❌ Failed to fetch data from VPS")
                        return False

                    streamed = isinstance(records, RangeFetchStreamedResult)
                    if streamed:
                        inserted = records.rows_upserted
                        print()
                    elif len(records) == 0:
                        print("\n✅ No new records to sync")
                        return True
                    else:
                        print()
                        inserted = insert_records(records)

                    if inserted > 0:
                        print("\n🔍 Verifying database...", end=" ", flush=True)
                        if verify_database():
                            print("✅")
                            cleanup_old_backups()
                            new_latest, new_count, _ = get_local_db_info()
                            print("\n" + "=" * 60)
                            log_message(f"✅ Sync completed successfully!")
                            log_message(f"   Added: {inserted:,} new records")
                            log_message(f"   Total records: {new_count:,}")
                            log_message(f"   Latest timestamp: {new_latest}")
                            print("=" * 60 + "\n")
                            return True
                        else:
                            print("❌")
                            log_message("❌ Database verification failed after sync")
                            return False
                    else:
                        print("\n⚠️  No records were inserted (all duplicates?)")
                        return True
                        
                except Exception as e:
                    log_message(f"⚠️  Error in smart gap detection: {e}")
                    log_message("📋 Falling back to regular incremental sync")
                    try:
                        records = fetch_incremental_data(local_latest, use_overlap=True, overlap_minutes=15, fetch_from_start_of_day=False)
                        if records is None:
                            print("\n❌ Failed to fetch data from VPS")
                            return False
                        streamed = isinstance(records, RangeFetchStreamedResult)
                        if streamed:
                            inserted = records.rows_upserted
                            print()
                        elif len(records) == 0:
                            print("\n✅ No new records to sync")
                            return True
                        else:
                            print()
                            inserted = insert_records(records)
                        if inserted > 0:
                            print("\n🔍 Verifying database...", end=" ", flush=True)
                            if verify_database():
                                print("✅")
                                cleanup_old_backups()
                                new_latest, new_count, _ = get_local_db_info()
                                print("\n" + "=" * 60)
                                log_message(f"✅ Sync completed successfully!")
                                log_message(f"   Added: {inserted:,} new records")
                                log_message(f"   Total records: {new_count:,}")
                                log_message(f"   Latest timestamp: {new_latest}")
                                print("=" * 60 + "\n")
                                return True
                            print("❌")
                            log_message("❌ Database verification failed after sync")
                            return False
                        print("\n⚠️  No records were inserted (all duplicates?)")
                        return True
                    except Exception as e2:
                        log_message(f"❌ Incremental fallback failed: {e2}")
                        return False
            else:
                # No earliest timestamp or reconcile path skipped
                if missing_on_local <= 0:
                    print("\n✅ Row counts match source.")
                    return True
                log_message("📋 Cannot determine earliest date for reconcile — incremental sync")
                records = fetch_incremental_data(local_latest, use_overlap=True, overlap_minutes=15, fetch_from_start_of_day=False)
                if records is None:
                    print("\n❌ Failed to fetch data from VPS")
                    return False
                streamed = isinstance(records, RangeFetchStreamedResult)
                if streamed:
                    inserted = records.rows_upserted
                    print()
                elif len(records) == 0:
                    print("\n✅ No new records to sync")
                    return True
                else:
                    print()
                    inserted = insert_records(records)
                if inserted > 0:
                    print("\n🔍 Verifying database...", end=" ", flush=True)
                    if verify_database():
                        print("✅")
                        cleanup_old_backups()
                        new_latest, new_count, _ = get_local_db_info()
                        print("\n" + "=" * 60)
                        log_message(f"✅ Sync completed successfully!")
                        log_message(f"   Added: {inserted:,} new records")
                        log_message(f"   Total records: {new_count:,}")
                        log_message(f"   Latest timestamp: {new_latest}")
                        print("=" * 60 + "\n")
                        return True
                    print("❌")
                    log_message("❌ Database verification failed after sync")
                    return False
                print("\n⚠️  No records were inserted (all duplicates?)")
                return True
    
    if not force and local_norm > vps_norm:
        print("\n⚠️  Local database is newer than VPS. Use --force to sync anyway.")
        return False
    
    # Calculate how many records to fetch
    print(f"\n📈 Gap detected: Local ends at {local_latest}")
    print(f"   VPS has data until {vps_latest}\n")
    
    # Create backup before sync
    print("💾 Creating backup...", end=" ", flush=True)
    backup_result = backup_local_db()
    if backup_result:
        print("✅")
    else:
        print("⚠️")
    
    # Fetch incremental data with overlap to catch records at same timestamp
    use_overlap = True
    fetch_from_start_of_day = False
    
    if local_latest < vps_latest:
        # VPS is ahead - use overlap to catch any records at the boundary
        use_overlap = True
    
    records = fetch_incremental_data(local_latest, use_overlap=use_overlap, overlap_minutes=15, fetch_from_start_of_day=fetch_from_start_of_day)
    
    if records is None:
        print("\n❌ Failed to fetch data from VPS")
        return False
    
    if len(records) == 0:
        print("\n✅ No new records to sync")
        return True
    
    print()  # Empty line before insertion progress
    
    # Insert records
    inserted = insert_records(records)
    
    if inserted > 0:
        # Verify
        print("\n🔍 Verifying database...", end=" ", flush=True)
        if verify_database():
            print("✅")
            # Cleanup old backups
            cleanup_old_backups()
            
            # Show final stats
            new_latest, new_count, _ = get_local_db_info()
            print("\n" + "=" * 60)
            log_message(f"✅ Sync completed successfully!")
            log_message(f"   Added: {inserted:,} new records")
            log_message(f"   Total records: {new_count:,}")
            log_message(f"   Latest timestamp: {new_latest}")
            print("=" * 60 + "\n")
            return True
        else:
            print("❌")
            log_message("❌ Database verification failed after sync")
            return False
    else:
        print("\n⚠️  No records were inserted (all duplicates?)")
        return True


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Incremental sync NIFTY database from VPS")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force sync even if local DB is newer"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run in auto mode (check every hour)"
    )
    parser.add_argument(
        "--backfill-volume",
        action="store_true",
        help="Backfill volume data for records with volume = 0 or NULL"
    )
    parser.add_argument(
        "--backfill-date",
        type=str,
        help="Backfill volume for specific date range (format: YYYY-MM-DD:YYYY-MM-DD)"
    )
    parser.add_argument(
        "--fresh-start",
        action="store_true",
        help="Delete local database and download everything fresh from VPS"
    )
    parser.add_argument(
        "--fill-gaps",
        type=str,
        help="Fill gaps for specific date range (format: YYYY-MM-DD:YYYY-MM-DD or YYYY-MM-DD for single date)"
    )
    parser.add_argument(
        "--trim-to-vps",
        action="store_true",
        help="Remove from local any records not in VPS (VPS is source of truth)"
    )
    
    args = parser.parse_args()
    
    if args.fresh_start:
        success = download_full_database_from_vps()
        sys.exit(0 if success else 1)
    
    if args.trim_to_vps:
        deleted = trim_local_to_match_vps()
        sys.exit(0 if deleted >= 0 else 1)
    
    if args.fill_gaps:
        # Fill gaps for specific date range
        try:
            if ':' in args.fill_gaps:
                start_date, end_date = args.fill_gaps.split(':')
            else:
                # Single date - fill gaps for that date only
                start_date = args.fill_gaps
                end_date = args.fill_gaps
            
            log_message(f"🔍 Filling gaps for date range: {start_date} to {end_date}")
            
            # Detect gaps
            broken_timestamps = detect_specific_gaps(start_date, end_date)
            
            if broken_timestamps is None:
                log_message("❌ Failed to detect gaps")
                sys.exit(1)
            
            if not broken_timestamps:
                log_message("✅ No gaps found for this date range")
                sys.exit(0)
            
            log_message(f"📋 Found {len(broken_timestamps)} broken timestamps")
            
            # Fetch missing records
            records = fetch_missing_records_for_date_range(start_date, end_date, broken_timestamps)
            
            if records is None:
                log_message("❌ Failed to fetch missing records")
                sys.exit(1)

            if isinstance(records, RangeFetchStreamedResult):
                if records.rows_upserted > 0:
                    log_message(
                        f"✅ Successfully filled gaps: {records.rows_upserted:,} records upserted "
                        f"({records.rows_fetched:,} streamed from remote)"
                    )
                else:
                    log_message("⚠️  No records were inserted (all duplicates?)")
                sys.exit(0)
            
            if len(records) == 0:
                log_message("✅ No records to insert")
                sys.exit(0)
            
            # Insert records
            print()  # Empty line before insertion progress
            inserted = insert_records(records)
            
            if inserted > 0:
                log_message(f"✅ Successfully filled gaps: {inserted:,} records inserted")
                sys.exit(0)
            else:
                log_message("⚠️  No records were inserted (all duplicates?)")
                sys.exit(0)
                
        except Exception as e:
            log_message(f"❌ Error filling gaps: {e}")
            import traceback
            log_message(traceback.format_exc())
            sys.exit(1)
    
    if args.backfill_volume:
        date_range = None
        if args.backfill_date:
            try:
                start_date, end_date = args.backfill_date.split(':')
                # Convert to full datetime format if needed
                if len(start_date) == 10:  # YYYY-MM-DD
                    start_date = f"{start_date}T00:00:00"
                if len(end_date) == 10:  # YYYY-MM-DD
                    end_date = f"{end_date}T23:59:59"
                date_range = (start_date, end_date)
                log_message(f"📅 Backfilling volume for date range: {start_date} to {end_date}")
            except:
                log_message("❌ Invalid date range format. Use: YYYY-MM-DD:YYYY-MM-DD")
                sys.exit(1)
        
        updated = backfill_volume_data(date_range)
        sys.exit(0 if updated >= 0 else 1)
    elif args.auto:
        log_message("🤖 Auto-sync mode enabled (checking every hour)")
        while True:
            sync_database(force=args.force)
            log_message(f"⏰ Next sync in 1 hour...")
            time.sleep(3600)  # Wait 1 hour
    else:
        success = sync_database(force=args.force)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    from tqdm import tqdm
    main()
