#!/usr/bin/env python3
"""
Data Sync & Verification Service (PostgreSQL)
- Detects gaps on startup
- Fills gaps from VPS
- Syncs with VPS every 5 minutes
- VPS data overwrites local on mismatch (VPS is source of truth)
"""

import sys
import subprocess
import json
import time
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db
from services.ssh_vps import SSH_KEY_PATH, ssh_base_argv, ssh_user_host

VPS_DB_PATH = os.getenv("VPS_DB_PATH", "/opt/nifty-data-collector/nifty_local.db")
SYNC_INTERVAL = 300  # 5 minutes

# Setup logging
log_file_path = Path(__file__).parent.parent / "data" / "sync_service.log"
log_file_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(log_file_path), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class DataSyncService:
    """Service for syncing with VPS and filling gaps (local PostgreSQL)"""
    
    def __init__(self):
        self.vps_db_path = VPS_DB_PATH
        self.ssh_key_path = SSH_KEY_PATH

        logger.info("Data Sync Service initialized")
    
    def get_local_latest_timestamp(self) -> str:
        """Get the latest timestamp from local PostgreSQL"""
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
            result = cursor.fetchone()
            conn.close()
            if result and result[0]:
                ts = result[0]
                return ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
            return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        except Exception as e:
            logger.debug(f"Error getting local latest timestamp: {e}")
            return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    
    def get_vps_latest_timestamp(self) -> tuple:
        """Get the latest timestamp and record count from VPS database
        Returns: (timestamp, count) or (None, None) if error"""
        try:
            if not Path(self.ssh_key_path).exists():
                logger.debug("SSH key not found")
                return (None, None)
            
            # Execute query on VPS using inline Python
            ssh_cmd = ssh_base_argv() + [
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                ssh_user_host(),
                f"python3 -c 'import sqlite3, json; conn = sqlite3.connect(\"{self.vps_db_path}\"); cursor = conn.cursor(); cursor.execute(\"SELECT MAX(ts) FROM ltp_ticks\"); ts = cursor.fetchone()[0]; cursor.execute(\"SELECT COUNT(*) FROM ltp_ticks\"); cnt = cursor.fetchone()[0]; conn.close(); print(json.dumps({{\"timestamp\": ts, \"count\": cnt}}))'",
            ]
            
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=15
            )
            
            if result.returncode == 0:
                try:
                    import json
                    data = json.loads(result.stdout.strip())
                    if "error" in data:
                        logger.error(f"VPS database error: {data['error']}")
                        return (None, None)
                    return (data.get("timestamp"), data.get("count"))
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse VPS response: {result.stdout}")
                    return (None, None)
            else:
                logger.error(f"SSH command failed: {result.stderr}")
                return (None, None)
        
        except subprocess.TimeoutExpired:
            logger.error("VPS connection timeout")
            return (None, None)
        except Exception as e:
            logger.debug(f"Error getting VPS latest timestamp: {e}")
            return (None, None)
    
    def get_market_open_time_today(self) -> datetime:
        """Get market open time for today (9:15 AM IST = 3:45 AM UTC)"""
        now = datetime.now(timezone.utc)
        # Convert to IST
        ist_offset = timedelta(hours=5, minutes=30)
        ist_now = now + ist_offset
        
        # Market opens at 9:15 AM IST
        market_open_ist = ist_now.replace(hour=9, minute=15, second=0, microsecond=0)
        
        # Convert back to UTC
        market_open_utc = market_open_ist - ist_offset
        
        return market_open_utc
    
    def detect_gap(self) -> tuple:
        """Detect if there's a gap in local data"""
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                logger.debug("ltp_ticks table does not exist - no gap to detect")
                return None
            conn.close()
            
            local_latest = self.get_local_latest_timestamp()
            local_latest_dt = datetime.fromisoformat(local_latest.replace('Z', '+00:00'))
            
            market_open = self.get_market_open_time_today()
            now = datetime.now(timezone.utc)
            
            # Check if we're past market open and local data is behind
            if now > market_open and local_latest_dt < market_open:
                logger.warning(f"Gap detected: Local data ends at {local_latest}, market opened at {market_open}")
                return (local_latest, now.isoformat())
            
            # Check if local data is more than 10 minutes old during market hours
            time_diff = (now - local_latest_dt).total_seconds()
            if time_diff > 600:  # 10 minutes
                logger.warning(f"Local data is {time_diff/60:.1f} minutes old")
                return (local_latest, now.isoformat())
            
            return None
        except Exception as e:
            logger.debug(f"Error detecting gap: {e}")
            return None
    
    def fetch_vps_data(self, start_ts: str, end_ts: str) -> list:
        """Fetch data from VPS for given time range"""
        logger.info(f"Fetching VPS data from {start_ts} to {end_ts}")
        
        # Create Python script to run on VPS
        export_script = f'''import sqlite3
import json

db_path = "{self.vps_db_path}"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

query = """
SELECT symbol, token, ts, ltp, bid, ask, volume, oi, 
       delta, gamma, theta, vega, iv, 
       COALESCE(source, 'ws') as source
FROM ltp_ticks 
WHERE ts >= '{start_ts}' AND ts <= '{end_ts}'
ORDER BY ts
"""

cursor.execute(query)
for row in cursor:
    record = dict(row)
    print(json.dumps(record))

conn.close()
'''
        
        try:
            # Create temp script on VPS
            temp_script = f"/tmp/fetch_data_{int(time.time())}.py"
            
            # Write script to VPS
            create_cmd = ssh_base_argv() + [
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                ssh_user_host(),
                f"cat > {temp_script} << 'EOFSCRIPT'\n{export_script}\nEOFSCRIPT",
            ]
            
            subprocess.run(create_cmd, capture_output=True, timeout=30, check=True)
            
            # Execute script
            exec_cmd = ssh_base_argv() + [
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                ssh_user_host(),
                f"python3 {temp_script} && rm {temp_script}",
            ]
            
            process = subprocess.Popen(
                exec_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=8192
            )
            
            records = []
            for line in process.stdout:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        records.append(record)
                    except json.JSONDecodeError:
                        continue
            
            process.wait()
            
            logger.info(f"Fetched {len(records)} records from VPS")
            return records
        
        except Exception as e:
            logger.error(f"Error fetching VPS data: {e}")
            return []
    
    def insert_vps_data(self, records: list, overwrite: bool = True):
        """Insert VPS data into local database"""
        if not records:
            logger.warning("No records to insert")
            return 0
        
        logger.info(f"Inserting {len(records)} records into local database (overwrite={overwrite})")
        
        conn = db.get_connection()
        cursor = conn.cursor()
        inserted = 0
        updated = 0
        
        if overwrite:
            upsert_sql = """
                INSERT INTO ltp_ticks 
                (symbol, token, ts, ltp, bid, ask, volume, oi, delta, gamma, theta, vega, iv, source)
                VALUES (%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(symbol, ts) DO UPDATE SET
                    token = excluded.token, ltp = excluded.ltp, bid = excluded.bid, ask = excluded.ask,
                    volume = excluded.volume, oi = excluded.oi, delta = excluded.delta, gamma = excluded.gamma,
                    theta = excluded.theta, vega = excluded.vega, iv = excluded.iv, source = excluded.source
            """
        else:
            upsert_sql = """
                INSERT INTO ltp_ticks 
                (symbol, token, ts, ltp, bid, ask, volume, oi, delta, gamma, theta, vega, iv, source)
                VALUES (%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(symbol, ts) DO NOTHING
            """
        
        for record in records:
            try:
                ts_val = db.ensure_utc_suffix(record.get('ts')) if record.get('ts') else record.get('ts')
                cursor.execute(upsert_sql, (
                    record.get('symbol'),
                    record.get('token'),
                    ts_val,
                    record.get('ltp'),
                    record.get('bid'),
                    record.get('ask'),
                    record.get('volume'),
                    record.get('oi'),
                    record.get('delta'),
                    record.get('gamma'),
                    record.get('theta'),
                    record.get('vega'),
                    record.get('iv'),
                    'vps_sync'
                ))
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.error(f"Error inserting record: {e}")
        
        conn.commit()
        conn.close()
        logger.info(f"Inserted: {inserted}")
        return inserted
    
    def fill_gap(self, start_ts: str, end_ts: str):
        """Fill gap in local database from VPS using incremental fetch (similar to sync_nifty_db.py)"""
        logger.info(f"Filling gap: {start_ts} to {end_ts}")
        
        try:
            # Adjust timestamp to start of day if needed (similar to sync_nifty_db logic)
            from datetime import datetime, timedelta, timezone
            start_dt = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            
            # If start is before today's market open, adjust to today's market open
            market_open = self.get_market_open_time_today()
            if start_dt < market_open:
                start_ts = market_open.isoformat()
                logger.info(f"Adjusted start timestamp to market open: {start_ts}")
            
            # Fetch data from VPS using incremental approach
            records = self.fetch_vps_data(start_ts, end_ts)
            
            if not records:
                logger.warning("No data fetched from VPS to fill gap")
                return 0
            
            # Insert into local database (VPS overwrites)
            count = self.insert_vps_data(records, overwrite=True)
            
            logger.info(f"Gap filled: {count} records")
            return count
        except Exception as e:
            logger.error(f"Error filling gap: {e}")
            raise
    
    def verify_and_sync(self):
        """Verify local data against VPS and sync"""
        logger.info("Starting VPS verification and sync...")
        
        # Get latest local timestamp
        local_latest = self.get_local_latest_timestamp()
        
        # Fetch new data from VPS (last 10 minutes for verification)
        start_ts = (datetime.fromisoformat(local_latest.replace('Z', '+00:00')) - timedelta(minutes=10)).isoformat()
        end_ts = datetime.now(timezone.utc).isoformat()
        
        records = self.fetch_vps_data(start_ts, end_ts)
        
        if records:
            # Insert/update (VPS overwrites local)
            count = self.insert_vps_data(records, overwrite=True)
            logger.info(f"Sync complete: {count} records synced/verified")
        else:
            logger.warning("No data fetched from VPS for verification")
    
    def check_and_fill_gaps_on_startup(self):
        """Check for gaps on startup and fill them"""
        logger.info("Checking for gaps on startup...")
        
        gap = self.detect_gap()
        
        if gap:
            start_ts, end_ts = gap
            logger.warning(f"Gap detected on startup: {start_ts} to {end_ts}")
            
            # Fill the gap
            count = self.fill_gap(start_ts, end_ts)
            
            if count > 0:
                logger.info(f"✅ Gap filled successfully: {count} records")
            else:
                logger.error("❌ Failed to fill gap")
        else:
            logger.info("✅ No gaps detected")
    
    def run_periodic_sync(self):
        """Run periodic sync with VPS (every 5 minutes)"""
        logger.info(f"Starting periodic sync (every {SYNC_INTERVAL} seconds)")
        
        while True:
            try:
                self.verify_and_sync()
                logger.info(f"Next sync in {SYNC_INTERVAL} seconds...")
                time.sleep(SYNC_INTERVAL)
            
            except KeyboardInterrupt:
                logger.info("Periodic sync stopped by user")
                break
            
            except Exception as e:
                logger.error(f"Error in periodic sync: {e}")
                time.sleep(60)  # Wait 1 minute before retry


def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("DATA SYNC & VERIFICATION SERVICE")
    logger.info("=" * 60)
    
    service = DataSyncService()
    
    # Check and fill gaps on startup
    service.check_and_fill_gaps_on_startup()
    
    # Run periodic sync
    service.run_periodic_sync()


if __name__ == "__main__":
    main()
