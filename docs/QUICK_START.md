# 🚀 Quick Start Guide

## Prerequisites

1. Python 3.11+ installed
2. SSH key configured for VPS access
3. Angel One API credentials (for VPS collector)

## Installation

```powershell
# Install dependencies
pip install -r requirements.txt

# Install VPS system dependencies
cd vps_system
pip install -r requirements.txt
cd ..
```

## Starting Services

### 1. Start VPS Data Collector

```powershell
cd vps_system
.\start_vps_collector.ps1
```

This collects NIFTY data every 5 seconds and writes to `data/nifty_local.db`.

### 2. Start WebSocket Broadcaster

```powershell
cd services
py websocket_broadcaster_service.py
```

This monitors the database and broadcasts updates to WebSocket clients.

### 3. Start All Services (Alternative)

```powershell
.\scripts\start_realtime_services.ps1
```

## Using in Your Projects

### Python Example

```python
from services.realtime_client import RealtimeDataClient

def on_data(data):
    print(f"Received: {data['symbol']} @ {data['ltp']}")

# Create client
client = RealtimeDataClient(on_data_callback=on_data)

# Connect to real-time feed
client.connect()

# Query historical data
latest = client.get_latest("NIFTY 50")
print(f"Latest NIFTY 50: {latest}")

# Keep running
import time
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    client.close()
```

## Testing

### Test WebSocket Connection

```powershell
py scripts\test_websocket_client.py
```

### Test Multiple Clients

```powershell
py scripts\test_multiple_clients.py
```

### Verify Data Broadcasting

```powershell
py scripts\verify_data_broadcasting.py
```

## Sync from VPS (Backup)

If you need to sync historical data from VPS:

```powershell
.\services\sync_nifty_db.ps1
```

## Documentation

- **Database Schema**: See `docs/DATABASE_SCHEMA_REFERENCE.md`
- **Architecture**: See `docs/ARCHITECTURE_IMPLEMENTATION_PLAN.md`
- **VPS Setup**: See `docs/VPS_SYSTEM_QUICK_START.md`
- **Project Structure**: See `PROJECT_STRUCTURE.md`

