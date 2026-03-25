#!/usr/bin/env python3
"""
Verify ltp_ticks timestamps on VPS against NSE trading hours (09:15–15:30 IST).
Read-only: no changes to VPS. Run locally; connects to VPS via SSH.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from services.ssh_vps import scp_base_argv, ssh_base_argv, ssh_user_host

VPS_DB_PATH = os.getenv("VPS_DB_PATH", "/opt/nifty-data-collector/nifty_local.db")

REMOTE_SCRIPT = '''
import sqlite3
db_path = "{db_path}"
conn = sqlite3.connect(db_path)
cur = conn.cursor()
q1 = "SELECT SUM(CASE WHEN time(datetime(ts,'+5 hours','30 minutes'))<'09:15:00' OR time(datetime(ts,'+5 hours','30 minutes'))>'15:30:00' THEN 1 ELSE 0 END), SUM(CASE WHEN time(datetime(ts,'+5 hours','30 minutes'))>='09:15:00' AND time(datetime(ts,'+5 hours','30 minutes'))<='15:30:00' THEN 1 ELSE 0 END), COUNT(*) FROM ltp_ticks"
cur.execute(q1)
row = cur.fetchone()
outside, inside, total = (row[0] or 0), (row[1] or 0), (row[2] or 0)
print("=== VPS ltp_ticks: Trading Hours (09:15-15:30 IST) ===")
print("Outside hours (<09:15 or >15:30 IST):", f"{{outside:,}}")
print("Inside hours (09:15-15:30 IST):     ", f"{{inside:,}}")
print("Total rows:                         ", f"{{total:,}}")
if total > 0:
    print("Outside-hours %:                    ", f"{{100*outside/total:.2f}}%")
q2 = "SELECT date(datetime(ts,'+5 hours','30 minutes')), COUNT(*) FROM ltp_ticks WHERE time(datetime(ts,'+5 hours','30 minutes'))<'09:15:00' OR time(datetime(ts,'+5 hours','30 minutes'))>'15:30:00' GROUP BY 1 ORDER BY 1"
cur.execute(q2)
rows = cur.fetchall()
if rows:
    print("\\nDates with out-of-market data:", len(rows))
    for r in rows[:15]:
        print(f"  {{r[0]}}: {{r[1]:,}} rows")
    if len(rows) > 15:
        print("  ... and", len(rows)-15, "more dates")
conn.close()
'''


def main():
    script = REMOTE_SCRIPT.format(db_path=VPS_DB_PATH)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        tmp = f.name
    try:
        subprocess.run(
            scp_base_argv()
            + [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=15",
                tmp,
                f"{ssh_user_host()}:/tmp/verify_hours.py",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        # run on VPS
        result = subprocess.run(
            ssh_base_argv()
            + [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=15",
                ssh_user_host(),
                "python3 /tmp/verify_hours.py",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print("Error:", result.stderr or result.stdout, file=sys.stderr)
            return 1
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print("SCP failed:", e.stderr.decode() if e.stderr else str(e), file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("Timeout.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        Path(tmp).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
