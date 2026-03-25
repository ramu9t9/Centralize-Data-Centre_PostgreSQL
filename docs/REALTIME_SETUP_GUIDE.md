# 🚀 Local Real-Time Data Centre - Setup Guide

## 📋 Prerequisites

1. **Python 3.8+** installed
2. **VPS running** (no changes needed)
3. **SSH key** configured (`~/.ssh/nifty_server_key`)

## 📦 Installation

### Step 1: Install Dependencies

```powershell
cd "G:\Projects\Centralize Data Centre"

# Install required packages
pip install websockets python-dotenv pyotp SmartApi-Python
```

### Step 2: Configure Environment

The `.env` file is already configured. Verify it contains:
```
HOSTINGER_API_TOKEN=...
VPS_IP=31.97.233.93
VPS_USER=root
DB_PATH=G:\Projects\Centralize Data Centre\data\nifty_local.db
```

## 🚀 Starting the Services

### Option 1: Start Both Services (Recommended)

```powershell
.\start_services.ps1
```

This starts:
- **Real-Time Data Service** (Angel One WebSocket → Local DB → Projects)
- **Data Sync Service** (Gap filling + VPS verification)

### Option 2: Start Services Individually

```powershell
# Start real-time data service only
.\start_services.ps1 -DataService

# Start sync service only
.\start_services.ps1 -SyncService
```

### Option 3: Manual Start

```powershell
# Terminal 1: Real-time service
py local_data_service.py

# Terminal 2: Sync service
py data_sync_service.py
```

## 📊 What Happens on Startup

1. **Gap Detection**
   - Checks if local data is behind
   - Detects gaps (e.g., if started late)

2. **Gap Filling**
   - Fetches missing data from VPS
   - Fills gaps in local database
   - VPS data overwrites local (source of truth)

3. **Real-Time Collection**
   - Connects to Angel One (Login #2)
   - Receives WebSocket data
   - Stores to local DB
   - Broadcasts to projects

4. **Periodic Sync**
   - Syncs with VPS every 5 minutes
   - Verifies data integrity
   - VPS overwrites on mismatch

## 🔌 Using in Your Projects

### Example 1: Real-Time Data

```python
from realtime_client import RealtimeDataClient

def on_data(data):
    print(f"Symbol: {data['symbol']}, LTP: {data['ltp']}")

# Create client
client = RealtimeDataClient(on_data_callback=on_data)

# Connect to real-time feed
client.connect()

# Keep running
import time
while True:
    time.sleep(1)
```

### Example 2: Historical Data

```python
from realtime_client import RealtimeDataClient

client = RealtimeDataClient()

# Get latest tick
latest = client.get_latest("NIFTY 50")
print(f"Latest NIFTY: {latest}")

# Get historical data
from datetime import datetime, timedelta
end = datetime.now()
start = end - timedelta(hours=1)

history = client.get_historical(
    "NIFTY02JAN2624000CE",
    start.isoformat(),
    end.isoformat()
)

print(f"Got {len(history)} historical records")
```

### Example 3: Custom Queries

```python
from realtime_client import RealtimeDataClient

client = RealtimeDataClient()

# Get all call options
calls = client.query(
    "SELECT * FROM ltp_ticks WHERE symbol LIKE '%CE%' ORDER BY ts DESC LIMIT 100"
)

print(f"Found {len(calls)} call options")
```

## 📁 File Structure

```
Centralize Data Centre/
├── local_data_service.py      # Main real-time service
├── data_sync_service.py        # Gap filling + VPS sync
├── realtime_client.py          # Client library for projects
├── angel_connector.py          # Angel One WebSocket (TODO: complete)
├── start_services.ps1          # Startup script
│
├── data/
│   ├── local_realtime.db       # Local real-time database
│   ├── local_service.log       # Service logs
│   └── sync_service.log        # Sync logs
│
└── (existing VPS sync files unchanged)
```

## 🔍 Monitoring

### Check Service Status

```powershell
# View real-time service logs
Get-Content "data\local_service.log" -Tail 20

# View sync service logs
Get-Content "data\sync_service.log" -Tail 20
```

### Check Database

```powershell
# Latest data
py -c "import sqlite3; conn = sqlite3.connect('data/local_realtime.db'); print(conn.execute('SELECT MAX(ts), COUNT(*) FROM ltp_ticks').fetchone())"

# Available symbols
py -c "import sqlite3; conn = sqlite3.connect('data/local_realtime.db'); print(conn.execute('SELECT DISTINCT symbol FROM ltp_ticks').fetchall())"
```

### Check WebSocket Server

```powershell
# Test connection
py -c "import websocket; ws = websocket.create_connection('ws://localhost:8765'); print('Connected!'); ws.close()"
```

## ⚠️ Troubleshooting

### Issue: WebSocket Connection Failed

**Solution**: Ensure `local_data_service.py` is running
```powershell
.\start_services.ps1 -DataService
```

### Issue: No Data in Database

**Solution**: Check if Angel One integration is complete
```powershell
# Check logs
Get-Content "data\local_service.log" -Tail 50
```

### Issue: Gap Not Filled

**Solution**: Verify VPS connection
```powershell
# Test SSH
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "echo 'Connected'"
```

## 🎯 Next Steps

1. **Complete Angel One Integration**
   - Update `angel_connector.py` with actual token lookup
   - Integrate with `local_data_service.py`

2. **Test Gap Filling**
   - Start service late (e.g., 11 AM)
   - Verify gap fills from VPS

3. **Integrate with Projects**
   - Update your trading projects to use `realtime_client.py`
   - Test concurrent access

4. **Set Up Auto-Start** (Optional)
   - Create Windows Task Scheduler task
   - Start services on PC boot

## 📞 Support

Check logs for errors:
- `data/local_service.log` - Real-time service
- `data/sync_service.log` - Sync service

---

**Status**: ✅ Core implementation complete
**TODO**: Complete Angel One WebSocket integration
