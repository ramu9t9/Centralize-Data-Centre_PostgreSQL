"""One-off: test sqlite3 ltp_ticks on VPS via SSH (uses .env)."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from services.ssh_vps import ssh_base_argv, ssh_user_host

p = os.getenv("VPS_DB_PATH", "")
cmd = ssh_base_argv() + [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    ssh_user_host(),
    f'sqlite3 {p} "SELECT COUNT(*) FROM ltp_ticks;"',
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
print("VPS_DB_PATH:", p)
print("returncode:", r.returncode)
print("stdout:", (r.stdout or "").strip()[:200])
print("stderr:", (r.stderr or "").strip()[:500])

cmd2 = ssh_base_argv() + [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    ssh_user_host(),
    "sqlite3 " + p + " .tables",
]
r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
print("--- .tables ---")
print("rc", r2.returncode, "out:", repr((r2.stdout or "")[:300]), "err:", repr((r2.stderr or "")[:300]))

cmd3 = ssh_base_argv() + [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    ssh_user_host(),
    "ls -la /opt/nifty-data-collector/ /opt/nifty-data-collector/data/ 2>&1 | head -40",
]
r3 = subprocess.run(cmd3, capture_output=True, text=True, timeout=30)
print("--- ls collector ---")
print((r3.stdout or r3.stderr or "")[:1200])

cmd4 = ssh_base_argv() + [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    ssh_user_host(),
    "ls -la /opt/nifty-data-collector/data/",
]
r4 = subprocess.run(cmd4, capture_output=True, text=True, timeout=30)
print("--- ls data/ ---")
print((r4.stdout or r4.stderr or "")[:2000])
print("--- If nifty_local.db is 0 bytes, use REMOTE_DATABASE_URL (Postgres on VPS). ---")
