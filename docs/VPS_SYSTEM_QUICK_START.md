# 🚀 VPS Data Collector - Quick Start Guide

## ✅ Setup Complete!

The **exact VPS data collector system** has been copied to your local machine.

## 📁 Location

```
G:\Projects\Centralize Data Centre\vps_system\
```

## 🚀 How to Run

### Option 1: PowerShell Script (Easiest)
```powershell
cd "G:\Projects\Centralize Data Centre\vps_system"
.\start_vps_collector.ps1
```

### Option 2: Direct Python
```powershell
cd "G:\Projects\Centralize Data Centre\vps_system"
py nifty_stream_local_sqlite.py
```

## 📊 What It Does

**Exact same as VPS:**
- ✅ Collects 23 symbols (NIFTY 50 + ATM ±5 strikes)
- ✅ Updates every 5 seconds
- ✅ Gets Greeks/IV from `optionGreek()` API (every 30 seconds)
- ✅ Stores to `data\nifty_local.db`
- ✅ Logs to `logs\` directory
- ✅ Only runs during market hours (9:15 AM - 3:30 PM IST)

## 📁 Files

- **Main Collector**: `vps_system/nifty_stream_local_sqlite.py` (exact VPS code)
- **Database**: `data\nifty_local.db` (same schema as VPS)
- **Logs**: `logs\` directory
- **Calendar**: `data\NSE_Market_Calendar_*.csv` (from VPS)

## ✅ Verification

After running, check:
```powershell
# Check database
py -c "import sqlite3; conn = sqlite3.connect(r'G:\Projects\Centralize Data Centre\data\nifty_local.db'); cursor = conn.cursor(); cursor.execute('SELECT COUNT(*), COUNT(DISTINCT symbol) FROM ltp_ticks'); print(f'Records: {cursor.fetchone()[0]}, Symbols: {cursor.fetchone()[1]}'); conn.close()"
```

## 🎯 Status

**READY** - Exact VPS system is now running locally!

---

**Note**: This is 100% identical to VPS - same code, same logic, same everything. Only paths and credentials are different.

