# 📋 Important Commands - Centralize Data Centre

## 🔄 Database Sync Commands

### Main Sync Script (Recommended)
```powershell
# Navigate to project directory
cd "G:\Projects\Centralize Data Centre"

# One-time sync (most common use)
.\sync_nifty_db.ps1

# Force sync (overwrite even if local is newer)
.\sync_nifty_db.ps1 -Force

# Auto-sync mode (checks every hour automatically)
.\sync_nifty_db.ps1 -Auto
```

### Python Direct Sync
```powershell
# One-time sync
py sync_nifty_db.py

# Force sync
py sync_nifty_db.py --force

# Auto-sync mode
py sync_nifty_db.py --auto

# Backfill volume data (if needed)
py sync_nifty_db.py --backfill-volume

# Backfill volume for specific date range
py sync_nifty_db.py --backfill-volume --backfill-date "2025-08-20:2025-08-25"
```

---

## 📊 Database Verification Commands

### Check Volume Data Status
```powershell
py check_volume_status.py
```

### Check IV (Implied Volatility) Data Status
```powershell
py check_iv_data.py
```

### Verify Final Database Status
```powershell
py verify_final_status.py
```

### Verify August 20 Start Time
```powershell
py verify_aug20_start.py
```

### Check August 19 Volume Timeline
```powershell
py check_aug19_timeline.py
```

### Check August 19 Complete Volume
```powershell
py check_aug19_complete_volume.py
```

---

## 🔍 Database Query Commands

### Quick Database Stats (PowerShell)
```powershell
# View latest sync log
Get-Content "G:\Projects\Centralize Data Centre\data\sync_log.txt" -Tail 20

# Check database info (total records, latest timestamp)
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); cursor = conn.cursor(); cursor.execute('SELECT MAX(ts), COUNT(*) FROM ltp_ticks'); print(cursor.fetchone())"
```

### Python Database Queries
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

---

## 🗑️ Database Cleanup Commands (Use with Caution!)

### Delete Records from August 14-18, 2025
```powershell
py delete_aug14_18_records.py
```

### Delete All Records from August 19, 2025
```powershell
py delete_aug19_complete.py
```

### Delete All Records Before August 29, 2025
```powershell
py delete_before_aug29.py
```

**⚠️ WARNING:** All deletion scripts create backups automatically, but use with caution!

---

## 🔐 SSH Commands (VPS Access)

### Connect to VPS
```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93
```

### Check VPS Database Size
```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "ls -lh /opt/nifty-data-collector/nifty_local.db"
```

### Check Recent Records on VPS
```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/nifty_local.db 'SELECT COUNT(*) FROM ltp_ticks WHERE ts > datetime(\"now\", \"-1 hour\")'"
```

### Get Latest Timestamp from VPS
```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/nifty_local.db 'SELECT MAX(ts) FROM ltp_ticks'"
```

### Download Entire Database from VPS (Manual)
```powershell
scp -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93:/opt/nifty-data-collector/nifty_local.db "G:\Projects\Centralize Data Centre\data\nifty_local_vps_backup.db"
```

---

## 📁 File Locations

### Database Files
```
G:\Projects\Centralize Data Centre\data\nifty_local.db          # Main database
G:\Projects\Centralize Data Centre\data\backups\                 # Backup directory
G:\Projects\Centralize Data Centre\data\sync_log.txt             # Sync logs
```

### Scripts
```
G:\Projects\Centralize Data Centre\sync_nifty_db.py             # Main sync script
G:\Projects\Centralize Data Centre\sync_nifty_db.ps1             # PowerShell wrapper
```

---

## ⚙️ Configuration

### Database Path
- **Local Database:** `G:\Projects\Centralize Data Centre\data\nifty_local.db`
- **VPS Database:** `/opt/nifty-data-collector/nifty_local.db`
- **VPS Host:** `31.97.233.93`
- **VPS User:** `root`
- **SSH Key:** `$env:USERPROFILE\.ssh\nifty_server_key`

### Edit Configuration
Edit `sync_nifty_db.py` to change:
- `VPS_HOST`
- `VPS_USER`
- `VPS_DB_PATH`
- `SSH_KEY_PATH`
- `LOCAL_DB_DIR`

---

## 📅 Scheduled Sync (Windows Task Scheduler)

### Create Scheduled Task via PowerShell
```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File `"G:\Projects\Centralize Data Centre\sync_nifty_db.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM
Register-ScheduledTask -TaskName "NIFTY Database Sync" -Action $action -Trigger $trigger -Description "Sync NIFTY database from VPS daily"
```

### Manual Task Scheduler Setup
1. Open Task Scheduler
2. Create Basic Task
3. Name: "NIFTY Database Sync"
4. Trigger: Daily at 6:00 PM (after market closes)
5. Action: Start a program
6. Program: `powershell.exe`
7. Arguments: `-File "G:\Projects\Centralize Data Centre\sync_nifty_db.ps1"`

---

## 🆘 Troubleshooting Commands

### Check Python Installation
```powershell
py --version
python --version
python3 --version
```

### Check SSH Key Exists
```powershell
Test-Path $env:USERPROFILE\.ssh\nifty_server_key
```

### Test SSH Connection
```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "echo 'Connection successful'"
```

### Check Database Integrity
```powershell
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); cursor = conn.cursor(); cursor.execute('PRAGMA integrity_check'); print(cursor.fetchone()[0])"
```

### View Recent Sync Logs
```powershell
Get-Content "G:\Projects\Centralize Data Centre\data\sync_log.txt" -Tail 50
```

---

## 📊 Quick Statistics Commands

### Total Records
```powershell
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); print(f\"Total: {conn.execute('SELECT COUNT(*) FROM ltp_ticks').fetchone()[0]:,}\")"
```

### Latest Timestamp
```powershell
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); print(f\"Latest: {conn.execute('SELECT MAX(ts) FROM ltp_ticks').fetchone()[0]}\")"
```

### Volume Coverage
```powershell
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); cursor = conn.cursor(); cursor.execute('SELECT COUNT(*) as total, COUNT(CASE WHEN volume > 0 THEN 1 END) as with_vol FROM ltp_ticks WHERE symbol LIKE \"%CE%\" OR symbol LIKE \"%PE%\"'); total, with_vol = cursor.fetchone(); print(f\"Volume Coverage: {with_vol:,}/{total:,} ({with_vol/total*100:.2f}%)\")"
```

---

## 🔄 Workflow Summary

### Daily Sync Workflow
1. **Run sync:** `.\sync_nifty_db.ps1`
2. **Check logs:** `Get-Content data\sync_log.txt -Tail 20`
3. **Verify status:** `py verify_final_status.py` (optional)

### First Time Setup
1. Ensure SSH key exists: `Test-Path $env:USERPROFILE\.ssh\nifty_server_key`
2. Test SSH connection: `ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93`
3. Run initial sync: `.\sync_nifty_db.ps1`
4. Verify database: `py verify_final_status.py`

### After Market Closes (Recommended)
- Run sync daily after market closes (around 4:00 PM IST / 6:00 PM if scheduled)
- Or set up scheduled task to run automatically

---

## 📝 Notes

- **Database starts from:** August 29, 2025 (with complete volume and IV data)
- **Volume coverage:** 100% from August 29 onwards
- **IV coverage:** 95%+ from August 29 onwards
- **Sync frequency:** Recommended daily after market closes
- **Backups:** Automatically created before each sync (keeps last 5)

---

**Last Updated:** December 21, 2025  
**Project:** Centralize Data Centre - NIFTY Database Sync
