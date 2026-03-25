# 📊 Centralize Data Centre (PostgreSQL) - NIFTY Database Sync

This project syncs NIFTY options data from the VPS server (SQLite) to your **local PostgreSQL** database. All local services use PostgreSQL; the VPS remains on SQLite.

## 📁 Folder Structure

See [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) for detailed structure.


```
Centralize Data Centre_PostgreSQL/
├── services/
│   ├── sync_nifty_db.py      # Incremental sync (VPS SQLite → local PostgreSQL)
│   ├── full_db_sync.py       # Full refresh
│   ├── db.py                 # PostgreSQL connection helpers
│   └── ...
├── data/                     # Logs and backup dir (PostgreSQL data lives in DB server)
│   ├── backups/              # pg_dump backups (optional)
│   └── sync_log.txt
├── .env.example              # Copy to .env and set DATABASE_URL
└── requirements.txt
```

## 🚀 Quick Start

### Method 1: Batch File (Easiest - Double-Click)

**Simply double-click `start_all_services.bat` in the root directory!**

This will automatically start:
- ✅ VPS Data Collector
- ✅ WebSocket Broadcaster Service

**Additional batch files:**
- `stop_all_services.bat` - Stop all services
- `check_services_status.bat` - Check if services are running

### Method 2: PowerShell (Windows - Recommended)

```powershell
# One-time sync
cd "G:\Projects\Centralize Data Centre"
.\services\sync_nifty_db.ps1

# Force sync (overwrite even if local is newer)
.\services\sync_nifty_db.ps1 -Force

# Auto-sync mode (checks every hour)
.\services\sync_nifty_db.ps1 -Auto
```

### Method 2: Python Direct

```bash
# One-time sync
cd "G:\Projects\Centralize Data Centre"
py services/sync_nifty_db.py

# Force sync
py services/sync_nifty_db.py --force

# Auto-sync mode
py services/sync_nifty_db.py --auto
```

## 📋 Features

- ✅ **Automatic Backup**: Creates backup before each sync
- ✅ **Smart Sync**: Only syncs if VPS has newer data
- ✅ **Verification**: Validates downloaded database
- ✅ **Logging**: All operations logged to `sync_log.txt`
- ✅ **Backup Management**: Keeps last 5 backups automatically
- ✅ **Progress Display**: Shows real-time sync progress

## 📦 Requirements

1. **PostgreSQL 14+** installed and running. Create the database (or run `scripts/dry_run_postgres.py` to create it automatically):
   ```sql
   CREATE DATABASE "Centralized_Index_Option_Data";
   ```
   Server: **Host** localhost, **Port** 5432. **User** nifty_app, **Password** nifty_app_pw.

2. **Environment:** Copy `.env.example` to `.env` (already set for nifty_app / Centralized_Index_Option_Data):
   ```
   DATABASE_URL=postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data
   ```

3. **Python packages:**
   ```bash
   pip install -r requirements.txt
   ```

## 🔧 Configuration

- **Local DB:** Set `DATABASE_URL` in `.env` (or environment). All services use this to connect to PostgreSQL.
- **VPS/Sync:** Optional env vars: `VPS_HOST`, `VPS_SSH_PORT` (default `22`; HostITSmart uses `7576`), `VPS_USER`, `VPS_DB_PATH`, `SSH_KEY_PATH`. Implemented in `services/ssh_vps.py`. Or set `REMOTE_DATABASE_URL` to sync from remote PostgreSQL instead of SSH+SQLite. See `Important_commands.md` for Hostinger vs HostITSmart SSH examples.

## 📊 Database

- Data is stored in your **PostgreSQL** instance (host/db from `DATABASE_URL`).
- Run a sync (e.g. `py services/sync_nifty_db.py`) to pull data from VPS into PostgreSQL. Tables and indexes are created automatically on first run.

## 📚 Documentation

For complete documentation, see the `docs/` folder:

- **WEBSOCKET_CLIENT_INTEGRATION_GUIDE.md** - How to connect and receive real-time data
  - Quick start guide
  - Using the client library
  - Direct WebSocket connection
  - Code examples (Python, JavaScript, C#)
  - Data format and examples
  - Error handling and troubleshooting

- **DATABASE_SCHEMA_REFERENCE.md** - Complete database schema documentation
  - Table structure and fields
  - Sample queries
  - Connection examples (Python, R, Node.js)
  - Best practices and optimization tips

- **PROJECT_STRUCTURE.md** - Detailed project organization

## 🔄 Scheduled Sync (Windows Task Scheduler)

### Create Scheduled Task:

1. Open Task Scheduler
2. Create Basic Task
3. Name: "NIFTY Database Sync"
4. Trigger: Daily at 6:00 PM (after market closes)
5. Action: Start a program
6. Program: `powershell.exe`
7. Arguments: `-File "G:\Projects\Centralize Data Centre\sync_nifty_db.ps1"`

Or use this PowerShell command:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File `"G:\Projects\Centralize Data Centre\sync_nifty_db.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM
Register-ScheduledTask -TaskName "NIFTY Database Sync" -Action $action -Trigger $trigger -Description "Sync NIFTY database from VPS daily"
```

## 📝 Usage Examples

### Example 1: Daily Manual Sync

```powershell
# Run this after market closes (around 4 PM IST)
cd "G:\Projects\Centralize Data Centre"
.\sync_nifty_db.ps1
```

### Example 2: Force Update

```powershell
# If you want to overwrite local database regardless
.\sync_nifty_db.ps1 -Force
```

### Example 3: Continuous Auto-Sync

```powershell
# Runs in background, checks every hour
.\sync_nifty_db.ps1 -Auto
```

## 🔍 Check Sync Status

```powershell
# View latest sync log
Get-Content "G:\Projects\Centralize Data Centre\sync_log.txt" -Tail 20

# Check database info
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); cursor = conn.cursor(); cursor.execute('SELECT MAX(ts), COUNT(*) FROM ltp_ticks'); print(cursor.fetchone())"
```

## 🛠️ Troubleshooting

### Issue: SSH Connection Failed

**Solution:**
1. Check SSH key exists: `Test-Path $env:USERPROFILE\.ssh\nifty_server_key`
2. Test SSH manually (Hostinger): `ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93` — HostITSmart: `ssh -i $env:USERPROFILE\.ssh\nifty_server_key -p 7576 root@103.168.18.35`
3. Verify VPS is accessible

### Issue: Download Timeout

**Solution:**
- Database might be large (>100MB)
- Check internet connection
- Try again later
- Consider downloading during off-peak hours

### Issue: Database Verification Failed

**Solution:**
- Check if backup exists in `data/backups/`
- Restore from backup if needed
- Contact support if issue persists

## 📈 Database Statistics

After sync, check database stats:

```python
import sqlite3
from pathlib import Path

db_path = Path(r"G:\Projects\Centralize Data Centre\data\nifty_local.db")
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Total records
cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
print(f"Total records: {cursor.fetchone()[0]:,}")

# Latest timestamp
cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
print(f"Latest data: {cursor.fetchone()[0]}")

# Date range
cursor.execute("SELECT MIN(ts), MAX(ts) FROM ltp_ticks")
min_ts, max_ts = cursor.fetchone()
print(f"Date range: {min_ts} to {max_ts}")

conn.close()
```

## 🔐 Security Notes

- SSH key is stored at `~\.ssh\nifty_server_key`
- Backups are stored locally (not uploaded anywhere)
- All operations are logged for audit trail
- Database is read-only after sync (no modifications)

## 📞 Support

For issues or questions:
1. Check `sync_log.txt` for error messages
2. Verify SSH connection manually
3. Check VPS database is accessible
4. Review backup files if sync fails

---

**Last Updated:** November 26, 2025  
**Version:** 1.0.0  
**Status:** Production Ready

