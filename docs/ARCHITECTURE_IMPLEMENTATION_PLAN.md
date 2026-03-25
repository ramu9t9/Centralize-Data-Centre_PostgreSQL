# 🏗️ Real-Time Architecture Implementation Plan

## 🎯 Goal

Implement the architecture from `realtime_architecture.md` (lines 385-413) **WITHOUT changing the working VPS data collector**.

---

## 📋 Current State

### ✅ What's Working (DO NOT CHANGE):
- **VPS Data Collector** (`vps_system/nifty_stream_local_sqlite.py`)
  - ✅ Collects data every 5 seconds
  - ✅ Writes to `data/nifty_local.db`
  - ✅ Gets all fields: LTP, Volume, OI, Greeks, IV
  - ✅ Working perfectly!

### ✅ What Already Exists:
- **`realtime_client.py`** - Client library for projects (ready to use)
- **`data_sync_service.py`** - VPS backup sync service (can be adapted)

### ⚠️ What's Missing:
- **WebSocket Broadcaster Service** - Reads from DB and broadcasts to projects

---

## 🏗️ Architecture Implementation

### Current Flow (Working):
```
VPS Data Collector (nifty_stream_local_sqlite.py)
    ↓ Writes every 5 seconds
data/nifty_local.db
```

### Target Flow (To Implement):
```
VPS Data Collector (nifty_stream_local_sqlite.py)  ← KEEP AS-IS
    ↓ Writes every 5 seconds
data/nifty_local.db
    ↓ Monitors for new records (NEW SERVICE)
WebSocket Broadcaster Service (websocket_broadcaster_service.py)
    ↓ Broadcasts via WebSocket
┌─────────┬─────────┬─────────┐
Project 1  Project 2  Project 3  (unlimited)
    ↑
    └─ Uses realtime_client.py (already exists)
       
VPS Backup Sync (data_sync_service.py)  ← Already exists
    - Syncs every 5 minutes
    - Fills gaps if local offline
```

---

## 📝 Implementation Plan

### Phase 1: WebSocket Broadcaster Service (NEW - Main Component)

**File**: `websocket_broadcaster_service.py`

**Purpose**: Monitor database for new records and broadcast to WebSocket clients

**Key Features**:
- ✅ Reads from `data/nifty_local.db` (where VPS writes)
- ✅ Monitors for new records every 5 seconds
- ✅ Broadcasts all fields (LTP, Volume, OI, Greeks, IV)
- ✅ Supports unlimited concurrent clients
- ✅ Low latency (<100ms from DB to client)
- ✅ Automatic client cleanup
- ✅ **Does NOT collect data** (VPS collector does that)

**Implementation Details**:

```python
class WebSocketBroadcaster:
    """
    Service that:
    1. Monitors data/nifty_local.db for new records
    2. Broadcasts to WebSocket clients (ws://localhost:8765)
    3. Does NOT collect data (VPS collector does that)
    """
    
    def __init__(self):
        self.db_path = "data/nifty_local.db"
        self.subscribers = set()
        self.last_ts = None  # Track last broadcast timestamp
        self.ws_port = 8765
    
    async def monitor_and_broadcast(self):
        """Monitor database and broadcast new records"""
        while True:
            # Query for new records since last broadcast
            new_records = self.get_new_records()
            
            # Broadcast to all clients
            for record in new_records:
                await self.broadcast(record)
            
            await asyncio.sleep(5)  # Check every 5 seconds
    
    def get_new_records(self):
        """Get new records from database since last broadcast"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if self.last_ts:
            query = "SELECT * FROM ltp_ticks WHERE ts > ? ORDER BY ts"
            cursor.execute(query, (self.last_ts,))
        else:
            # First run: get latest records
            query = "SELECT * FROM ltp_ticks ORDER BY ts DESC LIMIT 100"
            cursor.execute(query)
        
        records = []
        for row in cursor.fetchall():
            record = {
                'symbol': row[1],
                'token': row[2],
                'ts': row[3],
                'ltp': row[4],
                'bid': row[5],
                'ask': row[6],
                'volume': row[7],
                'oi': row[8],
                'delta': row[9],
                'gamma': row[10],
                'theta': row[11],
                'vega': row[12],
                'iv': row[13],
                'source': row[14]
            }
            records.append(record)
            # Update last timestamp
            if not self.last_ts or record['ts'] > self.last_ts:
                self.last_ts = record['ts']
        
        conn.close()
        return records
    
    async def broadcast(self, record):
        """Broadcast record to all connected clients"""
        if not self.subscribers:
            return
        
        message = json.dumps(record)
        disconnected = set()
        
        for subscriber in self.subscribers:
            try:
                await subscriber.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(subscriber)
            except Exception as e:
                logger.error(f"Error broadcasting: {e}")
                disconnected.add(subscriber)
        
        # Remove disconnected clients
        self.subscribers -= disconnected
```

---

### Phase 2: Update Existing Services (Minimal Changes)

#### 2.1: Update `data_sync_service.py` (if needed)

**Current**: Syncs `local_realtime.db`
**Update**: Sync `nifty_local.db` (where VPS writes)

**Changes**:
- Update `LOCAL_DB_PATH` to point to `data/nifty_local.db`
- Keep all other logic the same

#### 2.2: Verify `realtime_client.py`

**Status**: Already exists and should work
**Action**: Test connection to broadcaster service

---

### Phase 3: Service Launcher

**File**: `start_realtime_services.ps1`

**Purpose**: Start all services together

**Services**:
1. VPS Data Collector (already running separately)
2. WebSocket Broadcaster Service (NEW)
3. VPS Backup Sync Service (optional, can run separately)

---

## 📊 Complete Data Flow

```
┌─────────────────────────────────────────────┐
│  Angel One REST API                         │
│  (1 login from VPS collector)               │
└──────────────┬──────────────────────────────┘
               │ getMarketData("FULL") every 5s
               │ optionGreek() every 30s
               ↓
┌─────────────────────────────────────────────┐
│  VPS Data Collector                         │
│  (nifty_stream_local_sqlite.py)            │
│  - Collects every 5 seconds                 │
│  - Gets ALL fields (Greeks, IV)            │
│  - Writes to database                       │
└──────────────┬──────────────────────────────┘
               │ Writes every 5 seconds
               ↓
┌─────────────────────────────────────────────┐
│  data/nifty_local.db                        │
│  - All data stored here                     │
└──────────────┬──────────────────────────────┘
               │
               ├─→ WebSocket Broadcaster (NEW)
               │   - Monitors for new records
               │   - Broadcasts to clients
               │
               └─→ VPS Backup Sync (optional)
                   - Syncs every 5 min
                   - Fills gaps
               
┌─────────────────────────────────────────────┐
│  WebSocket Broadcaster Service              │
│  (websocket_broadcaster_service.py)        │
│  - Reads from database                      │
│  - Broadcasts to clients                   │
└──────┬──────────────────────────────────────┘
       │
       ├─→ Project 1 (WebSocket) <100ms
       ├─→ Project 2 (WebSocket) <100ms
       ├─→ Project 3 (WebSocket) <100ms
       └─→ ... unlimited
```

---

## 🔧 Implementation Steps

### Step 1: Create WebSocket Broadcaster Service

**File**: `websocket_broadcaster_service.py`

**Components**:
1. Database monitor (reads from `data/nifty_local.db`)
2. WebSocket server (broadcasts to clients on port 8765)
3. Record tracking (tracks last broadcast timestamp)
4. Client management (handles connections/disconnections)

**Key Functions**:
- `get_new_records()` - Query database for new records
- `broadcast()` - Send data to all clients
- `websocket_handler()` - Handle client connections
- `monitor_and_broadcast()` - Main loop

---

### Step 2: Update VPS Backup Sync (if needed)

**File**: `data_sync_service.py`

**Changes**:
- Update `LOCAL_DB_PATH` to `data/nifty_local.db`
- Keep all sync logic the same

---

### Step 3: Test Integration

**Test Steps**:
1. Start VPS collector (already running)
2. Start WebSocket broadcaster service
3. Connect test client using `realtime_client.py`
4. Verify data flow and latency

---

### Step 4: Create Service Launcher

**File**: `start_realtime_services.ps1`

**Purpose**: Start all services together

---

## ✅ Benefits of This Approach

1. **✅ No changes to VPS collector** - Keeps working as-is
2. **✅ Separation of concerns** - Data collection vs. distribution
3. **✅ Scalable** - Unlimited WebSocket clients
4. **✅ Low latency** - <100ms from DB to client
5. **✅ Reliable** - VPS collector continues independently
6. **✅ Backup** - VPS sync fills any gaps
7. **✅ Reuses existing code** - Client library already exists

---

## 📁 Files to Create

1. **`websocket_broadcaster_service.py`** - Main broadcaster service (NEW)
2. **`start_realtime_services.ps1`** - Service launcher (NEW)

---

## 📁 Files to Update (Minimal)

1. **`data_sync_service.py`** - Update DB path to `nifty_local.db` (if needed)

---

## 📁 Files to Keep Unchanged

1. **`vps_system/nifty_stream_local_sqlite.py`** - VPS data collector (working fine)
2. **`data/nifty_local.db`** - Database (VPS writes here)
3. **`realtime_client.py`** - Client library (already exists)

---

## 🎯 Success Criteria

- ✅ VPS collector continues working (no changes)
- ✅ WebSocket broadcaster reads from database
- ✅ Multiple projects can connect simultaneously
- ✅ Data latency <100ms from DB to client
- ✅ All fields broadcasted (LTP, Volume, OI, Greeks, IV)
- ✅ VPS sync fills gaps every 5 minutes (optional)

---

## 🚀 Implementation Order

1. **Step 1**: Create `websocket_broadcaster_service.py`
   - Database monitoring
   - WebSocket server
   - Broadcasting logic
   - Test with one client

2. **Step 2**: Test and verify
   - Verify data flow
   - Check latency
   - Test multiple clients

3. **Step 3**: Update VPS sync (if needed)
   - Update DB path
   - Test sync functionality

4. **Step 4**: Create launcher script
   - Start all services together
   - Service management

---

## 📊 Technical Details

### Database Monitoring

**Query for new records**:
```sql
SELECT * FROM ltp_ticks 
WHERE ts > ? 
ORDER BY ts
```

**First run** (get latest):
```sql
SELECT * FROM ltp_ticks 
ORDER BY ts DESC 
LIMIT 100
```

### WebSocket Protocol

**Port**: 8765
**Protocol**: JSON messages
**Message Format**:
```json
{
  "symbol": "NIFTY06JAN2626500CE",
  "token": "49873",
  "ts": "2026-01-02T07:47:00+00:00",
  "ltp": 7.15,
  "volume": 78521105,
  "oi": 5273840,
  "delta": 0.3840,
  "gamma": 0.0005,
  "theta": -35.21,
  "vega": 10.64,
  "iv": 5.85,
  "source": "api"
}
```

### Client Connection

**Using existing `realtime_client.py`**:
```python
from realtime_client import RealtimeDataClient

def on_data(data):
    print(f"Received: {data['symbol']} @ {data['ltp']}")

client = RealtimeDataClient(on_data_callback=on_data)
client.connect()
```

---

## 🔍 Next Steps

1. ✅ Create `websocket_broadcaster_service.py`
2. ✅ Test with one WebSocket client
3. ✅ Verify data flow and latency
4. ✅ Test multiple concurrent clients
5. ✅ Update VPS sync service (if needed)
6. ✅ Create service launcher

---

**Status**: Ready for implementation
**Risk**: Low (VPS collector unchanged, only adding broadcaster)
**Estimated Time**: 1-2 hours for full implementation
