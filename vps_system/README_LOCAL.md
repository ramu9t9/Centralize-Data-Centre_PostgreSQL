# VPS Data Collector - Local Copy

This is an **exact copy** of the VPS data collector system, adapted for local Windows use.

## What's Different

- **Database Path**: Uses local `data/nifty_local.db` instead of VPS path
- **Logs Directory**: Uses local `logs/` directory instead of `/opt/nifty-data-collector/logs`
- **Calendar Files**: Uses local `data/NSE_Market_Calendar_*.csv` files
- **Credentials**: Same as VPS (your credentials)

## What's the Same

- ✅ **Exact same code** - copied directly from VPS
- ✅ **Same logic** - all functions identical
- ✅ **Same API calls** - getMarketData("FULL") + optionGreek()
- ✅ **Same data structure** - identical database schema
- ✅ **Same collection method** - ATM ±5 strikes, 5-second intervals
- ✅ **Same Greeks/IV** - uses optionGreek API every 30 seconds

## Files

- `nifty_stream_local_sqlite.py` - Main data collector (exact VPS copy)
- `market_scheduler.py` - Market scheduler (if needed)
- `requirements.txt` - Python dependencies

## How to Run

```powershell
cd "G:\Projects\Centralize Data Centre\vps_system"
py nifty_stream_local_sqlite.py
```

## Database Location

- **Local**: `G:\Projects\Centralize Data Centre\data\nifty_local.db`
- **VPS**: `/opt/nifty-data-collector/nifty_local.db`

## Logs Location

- **Local**: `G:\Projects\Centralize Data Centre\logs\`
- **VPS**: `/opt/nifty-data-collector/logs/`

## Calendar Files

Downloaded from VPS to `data/` directory:
- `NSE_Market_Calendar_2025.csv`
- `NSE_Market_Calendar_2024_25.csv`

## Configuration

All settings are the same as VPS:
- `DATA_STORAGE_INTERVAL_SECS = 5` (5 seconds)
- `ATM_WINDOW = 5` (±5 strikes)
- `GREEKS_UPDATE_INTERVAL = 30` (30 seconds)

## Status

✅ **Ready to use** - Exact same system as VPS, just running locally!

