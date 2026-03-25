#!/usr/bin/env python3
"""
Full Database Sync into local PostgreSQL
- Source: remote PostgreSQL if REMOTE_DATABASE_URL is set, else VPS SQLite over SSH
- Truncates local ltp_ticks (and oi_snapshots) then refills from the source
"""

import subprocess
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import logging

# Allow importing services.db when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db
from services import remote_source
from services.ssh_vps import ssh_base_argv, ssh_user_host

# Path to SQLite on the VPS (SSH target: services/ssh_vps.py)
VPS_DB_PATH = os.getenv("VPS_DB_PATH", "/opt/nifty-data-collector/nifty_local.db")

PROJECT_ROOT = Path(__file__).parent.parent
LOCAL_DB_DIR = Path(os.getenv("LOCAL_DB_DIR", str(PROJECT_ROOT / "data")))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(LOCAL_DB_DIR / "backups")))
LOG_FILE = Path(os.getenv("FULL_SYNC_LOG_FILE", str(LOCAL_DB_DIR / "full_sync_log.txt")))

# Create directories if they don't exist
LOCAL_DB_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def _is_tty():
    """Return True if stderr is a real terminal (tqdm will use single-line update)."""
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def log_message(message: str):
    """Log message to file and console"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(message)
    logger.info(message)


def init_local_db() -> bool:
    """
    Initialize local PostgreSQL schema (tables and indexes).
    Safe to call multiple times (IF NOT EXISTS).
    """
    try:
        log_message("💾 Initializing local PostgreSQL schema...")
        conn = db.get_connection()
        db.init_postgres_schema(conn)
        conn.close()
        log_message("✅ Local database initialized")
        return True
    except Exception as e:
        log_message(f"❌ Failed to initialize local database: {e}")
        import traceback
        log_message(traceback.format_exc())
        return False


def upsert_ltp_ticks(conn, rows: List[Dict]) -> Tuple[int, int]:
    """
    UPSERT ltp_ticks records (insert or update all fields).
    ts from VPS is text; PostgreSQL accepts it for TIMESTAMPTZ.
    Returns: (inserted_count, updated_count)
    """
    if not rows:
        return (0, 0)
    
    cursor = conn.cursor()
    
    # Get count before
    cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
    count_before = cursor.fetchone()[0]
    
    # UPSERT: PostgreSQL placeholders %s
    upsert_sql = """
        INSERT INTO ltp_ticks 
        (symbol, token, ts, ltp, bid, ask, volume, oi,
         delta, gamma, theta, vega, iv, source)
        VALUES (%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(symbol, ts) DO UPDATE SET
            token = excluded.token,
            ltp = excluded.ltp,
            bid = excluded.bid,
            ask = excluded.ask,
            volume = excluded.volume,
            oi = excluded.oi,
            delta = excluded.delta,
            gamma = excluded.gamma,
            theta = excluded.theta,
            vega = excluded.vega,
            iv = excluded.iv,
            source = excluded.source
    """
    
    data_tuples = [
        (
            row.get('symbol'),
            row.get('token'),
            db.ensure_utc_suffix(row.get('ts')) if row.get('ts') else row.get('ts'),
            row.get('ltp'),
            row.get('bid'),
            row.get('ask'),
            row.get('volume'),
            row.get('oi'),
            row.get('delta'),
            row.get('gamma'),
            row.get('theta'),
            row.get('vega'),
            row.get('iv', 0.0),
            row.get('source', 'ws')
        )
        for row in rows
    ]
    
    batch_size = 1000
    for i in range(0, len(data_tuples), batch_size):
        batch = data_tuples[i:i + batch_size]
        cursor.executemany(upsert_sql, batch)
    
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
    count_after = cursor.fetchone()[0]
    actual_inserted = count_after - count_before
    updated = len(rows) - actual_inserted if actual_inserted < len(rows) else 0
    return (actual_inserted, updated)


def upsert_oi_snapshots(conn, rows: List[Dict]) -> Tuple[int, int]:
    """
    UPSERT oi_snapshots records (insert or update all fields).
    Returns: (inserted_count, updated_count)
    """
    if not rows:
        return (0, 0)
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM oi_snapshots")
    count_before = cursor.fetchone()[0]
    
    upsert_sql = """
        INSERT INTO oi_snapshots 
        (symbol, token, ts, oi, volume, delta, gamma, theta, vega, iv)
        VALUES (%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(symbol, ts) DO UPDATE SET
            token = excluded.token,
            oi = excluded.oi,
            volume = excluded.volume,
            delta = excluded.delta,
            gamma = excluded.gamma,
            theta = excluded.theta,
            vega = excluded.vega,
            iv = excluded.iv
    """
    
    data_tuples = [
        (
            row.get('symbol'),
            row.get('token'),
            db.ensure_utc_suffix(row.get('ts')) if row.get('ts') else row.get('ts'),
            row.get('oi'),
            row.get('volume'),
            row.get('delta'),
            row.get('gamma'),
            row.get('theta'),
            row.get('vega'),
            row.get('iv', 0.0)
        )
        for row in rows
    ]
    
    batch_size = 1000
    for i in range(0, len(data_tuples), batch_size):
        cursor.executemany(upsert_sql, data_tuples[i:i + batch_size])
    
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM oi_snapshots")
    count_after = cursor.fetchone()[0]
    actual_inserted = count_after - count_before
    updated = len(rows) - actual_inserted if actual_inserted < len(rows) else 0
    return (actual_inserted, updated)


def _pg_ts_to_tick_str(val) -> Optional[str]:
    """Remote timestamptz → UTC 'YYYY-MM-DDTHH:MM:SS' for upsert_ltp_ticks."""
    if val is None:
        return None
    if hasattr(val, "strftime"):
        dt = val
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    s = str(val).strip()
    return s[:19] if len(s) >= 19 and "T" in s else s


def get_vps_table_info(table: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Source table info: (min_ts, max_ts, count). Remote PG or VPS SQLite."""
    if remote_source.uses_remote_postgres():
        try:
            conn = remote_source.connect_remote()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT to_char(MIN(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS'), "
                    f"to_char(MAX(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS'), COUNT(*) FROM {table}"
                )
                row = cur.fetchone()
            finally:
                conn.close()
            if row and row[2] is not None:
                return row[0], row[1], int(row[2])
            return None, None, None
        except Exception as e:
            log_message(f"❌ Error getting remote PostgreSQL {table} info: {e}")
            return None, None, None

    try:
        cmd = ssh_base_argv() + [
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            ssh_user_host(),
            f"sqlite3 {VPS_DB_PATH} \"SELECT MIN(ts), MAX(ts), COUNT(*) FROM {table};\"",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            parts = result.stdout.strip().split("|")
            if len(parts) == 3:
                return parts[0], parts[1], int(parts[2])

        return None, None, None

    except Exception as e:
        log_message(f"❌ Error getting VPS {table} info: {e}")
        return None, None, None


def get_local_table_info(table: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Get local table info: (min_ts, max_ts, count). ts returned as ISO string for comparison with VPS."""
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        # Return timestamps as ISO strings to match VPS format
        cur.execute(
            f"SELECT to_char(MIN(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS'), "
            f"to_char(MAX(ts), 'YYYY-MM-DD\"T\"HH24:MI:SS'), COUNT(*) FROM {table}"
        )
        row = cur.fetchone()
        conn.close()
        if row and row[2] is not None:
            return row[0], row[1], row[2]
        return None, None, None
    except Exception as e:
        log_message(f"❌ Error getting local {table} info: {e}")
        return None, None, None


def full_refresh_ltp_from_remote_pg(local_conn, vps_count: int) -> bool:
    """Stream all ltp_ticks from REMOTE_DATABASE_URL into local_conn (batched UPSERT)."""
    from psycopg2.extras import RealDictCursor

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None  # type: ignore

    total_fetched = 0
    total_inserted = 0
    total_updated = 0
    batch_num = 0
    start_time = time.time()
    batch_size = 10000
    batch: List[Dict] = []
    rconn = None

    try:
        rconn = remote_source.connect_remote()
        with rconn.cursor() as _s:
            _s.execute("SET statement_timeout = 0")
        cur = rconn.cursor(name="full_ltp_remote", cursor_factory=RealDictCursor)
        cur.itersize = 10000
        cur.execute(
            """
            SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                   delta, gamma, theta, vega, iv, COALESCE(source, 'ws') AS source
            FROM ltp_ticks
            ORDER BY ts
            """
        )
        pbar = None
        if tqdm:
            pbar = tqdm(
                total=vps_count,
                desc="Downloading",
                unit="records",
                miniters=max(1, vps_count // 100) if vps_count else 1,
                disable=not _is_tty(),
            )

        for row in cur:
            rec = dict(row)
            rec["ts"] = _pg_ts_to_tick_str(rec.get("ts"))
            batch.append(rec)
            if len(batch) >= batch_size:
                batch_num += 1
                inserted, updated = upsert_ltp_ticks(local_conn, batch)
                total_inserted += inserted
                total_updated += updated
                total_fetched += len(batch)
                if pbar:
                    pbar.update(len(batch))
                elapsed = time.time() - start_time
                rate = total_fetched / elapsed if elapsed > 0 else 0
                log_message(
                    f"   📦 Batch {batch_num}: {len(batch):,} records "
                    f"({inserted:,} new, {updated:,} updated, {rate:.0f} rec/s)"
                )
                batch = []

        if batch:
            batch_num += 1
            inserted, updated = upsert_ltp_ticks(local_conn, batch)
            total_inserted += inserted
            total_updated += updated
            total_fetched += len(batch)
            if pbar:
                pbar.update(len(batch))
            log_message(
                f"   📦 Final batch: {len(batch):,} records "
                f"({inserted:,} new, {updated:,} updated)"
            )

        cur.close()
        if pbar:
            pbar.close()

        elapsed_total = time.time() - start_time
        if elapsed_total > 0:
            log_message(
                f"\n✅ Remote PostgreSQL pull: {total_fetched:,} rows in {elapsed_total / 60:.1f} min "
                f"({total_fetched / elapsed_total:.0f} rec/s)"
            )
        return True
    except Exception as e:
        log_message(f"❌ Error streaming from remote PostgreSQL: {e}")
        import traceback

        log_message(traceback.format_exc())
        return False
    finally:
        if rconn is not None:
            try:
                rconn.close()
            except Exception:
                pass


def _download_ltp_ticks_from_vps_sqlite(conn, vps_count: int) -> bool:
    """SSH + SQLite: stream all ltp_ticks JSON lines into conn (batched UPSERT)."""
    total_fetched = 0
    total_inserted = 0
    total_updated = 0
    batch_num = 0
    start_time = time.time()

    export_script = f'''import sqlite3
import json
import sys

db_path = "{VPS_DB_PATH}"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

query = """
SELECT symbol, token, ts, ltp, bid, ask, volume, oi, 
       delta, gamma, theta, vega, iv, COALESCE(source, 'ws') as source
FROM ltp_ticks
ORDER BY ts
"""

cursor.execute(query)
for row in cursor:
    record = dict(row)
    print(json.dumps(record))

conn.close()
'''

    temp_script = f"/tmp/export_full_{int(time.time())}.py"
    create_cmd = ssh_base_argv() + [
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=60",
        "-o", "ServerAliveCountMax=10",
        ssh_user_host(),
        f"cat > {temp_script} << 'EOFSCRIPT'\n{export_script}\nEOFSCRIPT",
    ]

    try:
        subprocess.run(create_cmd, capture_output=True, timeout=180, check=True)
    except Exception as e:
        log_message(f"❌ Failed to create export script on VPS: {e}")
        return False

    exec_cmd = ssh_base_argv() + [
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=120",
        "-o", "TCPKeepAlive=yes",
        ssh_user_host(),
        f"python3 {temp_script} 2>/dev/null; rm -f {temp_script}",
    ]

    process = subprocess.Popen(
        exec_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=16384,
    )

    batch: List[Dict] = []
    batch_size = 10000

    try:
        from tqdm import tqdm

        pbar = tqdm(
            total=vps_count,
            desc="Downloading",
            unit="records",
            miniters=max(1, vps_count // 100),
            disable=not _is_tty(),
        )
    except ImportError:
        pbar = None
        log_message("   (Progress bar not available, install tqdm for better progress display)")

    last_activity_time = time.time()
    max_idle_time = 600

    try:
        for line in process.stdout:
            if time.time() - last_activity_time > max_idle_time:
                process.terminate()
                log_message(f"❌ Download timeout: No data received for {max_idle_time} seconds")
                return False

            line = line.strip()
            if line:
                last_activity_time = time.time()
                try:
                    record = json.loads(line)
                    batch.append(record)

                    if len(batch) >= batch_size:
                        batch_num += 1
                        inserted, updated = upsert_ltp_ticks(conn, batch)
                        total_inserted += inserted
                        total_updated += updated
                        total_fetched += len(batch)

                        if pbar:
                            pbar.update(len(batch))

                        elapsed = time.time() - start_time
                        rate = total_fetched / elapsed if elapsed > 0 else 0
                        log_message(
                            f"   📦 Batch {batch_num}: {len(batch):,} records "
                            f"({inserted:,} new, {updated:,} updated, {rate:.0f} rec/s)"
                        )

                        batch = []
                except json.JSONDecodeError:
                    continue

        if batch:
            batch_num += 1
            inserted, updated = upsert_ltp_ticks(conn, batch)
            total_inserted += inserted
            total_updated += updated
            total_fetched += len(batch)
            if pbar:
                pbar.update(len(batch))
            log_message(
                f"   📦 Final batch: {len(batch):,} records "
                f"({inserted:,} new, {updated:,} updated)"
            )

        if pbar:
            pbar.close()

        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            log_message("⚠️  Process did not finish in time")

        if process.returncode != 0:
            stderr = process.stderr.read()
            if stderr:
                log_message(f"⚠️  Process warning: {stderr}")
    except Exception as e:
        log_message(f"❌ Error during streaming: {e}")
        import traceback

        log_message(traceback.format_exc())
        return False

    return True


def full_refresh_from_vps() -> bool:
    """
    Full refresh: Truncate local PostgreSQL tables and download everything from VPS.
    Uses streaming approach to download ALL records, then UPSERTs them in batches.
    """
    log_message("=" * 60)
    log_message("🔄 Starting FULL Database Refresh into local PostgreSQL")
    log_message("=" * 60)
    
    # Optional: run pg_dump manually before refresh if you need a backup
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    log_message("💾 For backup, run: pg_dump $DATABASE_URL > data/backups/backup_YYYYMMDD.sql")
    
    # Ensure schema exists, then truncate
    if not init_local_db():
        return False
    
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE ltp_ticks")
            cur.execute("TRUNCATE TABLE oi_snapshots")
        conn.commit()
        log_message("🗑️  Local tables truncated")
    except Exception as e:
        log_message(f"❌ Truncate failed: {e}")
        conn.close()
        return False
    
    # Get VPS table info
    log_message("\n📡 Getting VPS database info...")
    vps_min_ts, vps_max_ts, vps_count = get_vps_table_info('ltp_ticks')
    
    if vps_min_ts is None or vps_count is None:
        log_message("❌ Cannot get VPS database info")
        conn.close()
        return False
    
    _src = "remote PostgreSQL" if remote_source.uses_remote_postgres() else "VPS (SSH + SQLite)"
    log_message(f"📊 Source ltp_ticks ({_src}): {vps_count:,} records")
    log_message(f"   Earliest: {vps_min_ts}")
    log_message(f"   Latest: {vps_max_ts}")

    if remote_source.uses_remote_postgres():
        log_message(f"\n📥 Downloading ALL ltp_ticks from remote PostgreSQL...")
        log_message(f"   This may take a while for large databases ({vps_count:,} records)...")
        if not full_refresh_ltp_from_remote_pg(conn, vps_count):
            conn.close()
            return False
    else:
        log_message(f"\n📥 Downloading ALL ltp_ticks from VPS...")
        log_message(f"   This may take a while for large databases ({vps_count:,} records)...")
        if not _download_ltp_ticks_from_vps_sqlite(conn, vps_count):
            conn.close()
            return False

    conn.close()

    # Verify
    log_message("\n🔍 Verifying local database...")
    local_min_ts, local_max_ts, local_count = get_local_table_info('ltp_ticks')
    
    if local_count != vps_count:
        log_message(f"❌ Count mismatch!")
        log_message(f"   VPS: {vps_count:,} records")
        log_message(f"   Local: {local_count:,} records")
        log_message(f"   Difference: {vps_count - local_count:,} records")
        log_message("   Run: py -3 services/sync_nifty_db.py to fetch the remaining records (incremental sync).")
        return False
    
    if local_min_ts != vps_min_ts or local_max_ts != vps_max_ts:
        log_message(f"⚠️  Timestamp range mismatch!")
        log_message(f"   VPS: {vps_min_ts} to {vps_max_ts}")
        log_message(f"   Local: {local_min_ts} to {local_max_ts}")
        # Continue anyway (counts match is more important)
    
    log_message(f"✅ Verification passed!")
    log_message(f"   Local: {local_count:,} records")
    log_message(f"   VPS: {vps_count:,} records")
    log_message(f"   ✅ Counts match exactly!")
    
    elapsed_total = time.time() - start_time
    log_message(f"\n" + "=" * 60)
    log_message(f"✅ Full refresh completed successfully!")
    log_message(f"   Total fetched: {total_fetched:,} records")
    log_message(f"   Total inserted: {total_inserted:,} records")
    log_message(f"   Total updated: {total_updated:,} records")
    log_message(f"   Time elapsed: {elapsed_total/60:.1f} minutes")
    log_message(f"   Average speed: {total_fetched/elapsed_total:.0f} records/second")
    log_message("=" * 60)
    
    return True


def compare_vps_local() -> Dict:
    """Compare VPS and Local databases - diagnostics"""
    log_message("\n🔍 Comparing VPS vs Local databases...")
    
    vps_min, vps_max, vps_count = get_vps_table_info('ltp_ticks')
    local_min, local_max, local_count = get_local_table_info('ltp_ticks')
    
    result = {
        'vps_count': vps_count,
        'local_count': local_count,
        'count_match': vps_count == local_count if (vps_count and local_count) else False,
        'vps_min_ts': vps_min,
        'vps_max_ts': vps_max,
        'local_min_ts': local_min,
        'local_max_ts': local_max,
        'ts_range_match': (vps_min == local_min and vps_max == local_max) if (vps_min and local_min) else False
    }
    
    log_message(f"📊 VPS: {vps_count:,} records ({vps_min} to {vps_max})")
    log_message(f"📊 Local: {local_count:,} records ({local_min} to {local_max})")
    
    if result['count_match']:
        log_message("✅ Counts match!")
    else:
        diff = (vps_count - local_count) if (vps_count and local_count) else 0
        log_message(f"❌ Count mismatch: {diff:,} records difference")
    
    if result['ts_range_match']:
        log_message("✅ Timestamp ranges match!")
    else:
        log_message("⚠️  Timestamp ranges differ")
    
    return result


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Full database refresh from VPS")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare VPS vs Local (diagnostics only)"
    )
    
    args = parser.parse_args()
    
    if args.compare:
        compare_vps_local()
        return
    
    # Full refresh
    success = full_refresh_from_vps()
    
    if success:
        # Run comparison after refresh
        compare_vps_local()
    
    import sys
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
