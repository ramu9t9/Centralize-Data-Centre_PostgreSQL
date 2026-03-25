# 🎬 Live/Replay Broadcast Modes - Implementation Plan

## 🎯 Objective

Enable trading projects to test with **real historical data** broadcasted at realistic intervals (every 5 seconds), allowing backtesting outside market hours without code changes.

---

## 💡 Concept

**Current**: Projects connect to `ws://localhost:8765` and receive live data during market hours only.

**Enhanced**: Projects connect to same WebSocket but can receive either:
- **Live Mode**: Real-time data from Angel One (current implementation)
- **Replay Mode**: Historical data from database, broadcasted at 5-second intervals

**Key Benefit**: Projects don't need ANY code changes - they just receive data and process it!

---

## 🏗️ Architecture Design

### Option 1: Single Service with Mode Toggle (RECOMMENDED)

```
┌─────────────────────────────────────────────┐
│  Data Broadcast Control Panel (Tkinter)    │
│  ┌─────────┬─────────┐                     │
│  │  Live   │ Replay  │ ← Mode Selection    │
│  └─────────┴─────────┘                     │
│  Date: [2025-12-20] to [2025-12-21]        │
│  Speed: [1x] [2x] [5x] [10x]               │
│  [▶ Play] [⏸ Pause] [⏹ Stop]              │
│  Progress: ████████░░░░░░ 65%              │
└─────────────────┬───────────────────────────┘
                  │ Controls
                  ↓
┌─────────────────────────────────────────────┐
│  Enhanced Local Data Service                │
│                                              │
│  If Live Mode:                              │
│    → Fetch from Angel One REST API          │
│                                              │
│  If Replay Mode:                            │
│    → Read from local_realtime.db            │
│    → Broadcast at selected speed            │
│                                              │
│  → Broadcast to ws://localhost:8765         │
└──────────────┬──────────────────────────────┘
               │
               ├─→ Project 1 (No changes needed!)
               ├─→ Project 2 (No changes needed!)
               └─→ Project 3 (No changes needed!)
```

**Advantages**:
- ✅ Single WebSocket endpoint (8765)
- ✅ Projects don't need ANY changes
- ✅ Simple architecture
- ✅ Easy mode switching

---

### Option 2: Dual Service (Live + Replay)

```
Live Service (port 8765)    Replay Service (port 8766)
      ↓                              ↓
Projects connect to Live    Projects connect to Replay
```

**Advantages**:
- ✅ Can run both simultaneously
- ✅ More flexible

**Disadvantages**:
- ❌ Projects need to change connection port
- ❌ More complex

---

## ✅ RECOMMENDED: Option 1 - Single Service with Mode Toggle

---

## 🎨 Tkinter Control Panel Design

### Main Window:

```
┌──────────────────────────────────────────────────────┐
│  📡 NIFTY Data Broadcast Control                     │
├──────────────────────────────────────────────────────┤
│                                                       │
│  Mode Selection:                                     │
│  ┌──────────────┬──────────────┐                    │
│  │ ● Live       │ ○ Replay     │                    │
│  └──────────────┴──────────────┘                    │
│                                                       │
│  ─────────────── Replay Settings ──────────────────  │
│                                                       │
│  Start Date:  [2025-12-20 ▼] [09:15 ▼]             │
│  End Date:    [2025-12-21 ▼] [15:30 ▼]             │
│                                                       │
│  Symbols:  ☑ All  ☐ NIFTY 50  ☐ Options Only       │
│                                                       │
│  Playback Speed:                                     │
│  ┌────┬────┬────┬─────┬─────┐                      │
│  │ 1x │ 2x │ 5x │ 10x │ Max │                      │
│  └────┴────┴────┴─────┴─────┘                      │
│                                                       │
│  ─────────────── Controls ────────────────────────   │
│                                                       │
│  [▶ Start] [⏸ Pause] [⏹ Stop] [↺ Reset]            │
│                                                       │
│  ─────────────── VPS Sync ─────────────────────────   │
│                                                       │
│  Local DB Status:                                    │
│  • Last Record:   2026-01-02 15:30:00               │
│  • Total Records: 1,234,567                         │
│  • Gap Detected:  🔴 Yes (2026-01-02 12:00 - 14:30) │
│                                                       │
│  VPS DB Status:                                      │
│  • Last Record:   2026-01-02 20:00:00               │
│  • Available:     ✅ Connected                       │
│                                                       │
│  [🔄 Sync with VPS] [📊 Check Gaps] [⚙️ Auto-Sync]  │
│                                                       │
│  Last Sync:       2026-01-02 18:30:00               │
│  Records Added:   12,345                            │
│                                                       │
│  ─────────────── Status ──────────────────────────   │
│                                                       │
│  Current Mode:   🟢 Live / 🔵 Replay                │
│  Broadcasting:   ● Active / ○ Stopped               │
│  Current Time:   2025-12-20 10:45:23                │
│  Records Sent:   1,234 / 5,678                      │
│  Progress:       ████████░░░░░░ 21.7%               │
│  Clients:        3 connected                        │
│                                                       │
│  ─────────────── Logs ────────────────────────────   │
│                                                       │
│  [2025-12-20 10:45:23] Replay started               │
│  [2025-12-20 10:45:28] Broadcasting 23 symbols      │
│  [2025-12-20 10:45:33] 3 clients connected          │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## 📋 Features

### Core Features:

1. **Mode Selection**
   - Live: Real-time Angel One data
   - Replay: Historical data from database

2. **Replay Controls**
   - Date/time range selection
   - Play/Pause/Stop/Reset
   - Speed control (1x, 2x, 5x, 10x, Max)
   - Progress indicator

3. **VPS Sync & Gap Filling** ⭐ NEW
   - Manual sync button: "Sync with VPS"
   - Auto-detect gaps on startup
   - Display gap status (dates/times)
   - Show local vs VPS database status
   - One-click gap filling
   - Auto-sync mode (periodic)

4. **Symbol Filtering**
   - All symbols
   - NIFTY 50 only
   - Options only
   - Custom selection

5. **Status Display**
   - Current mode
   - Broadcasting status
   - Current replay time
   - Records sent/total
   - Connected clients count
   - Gap detection status
   - Last sync time

### Advanced Features (Optional):

1. **Loop Mode**
   - Repeat replay continuously
   - Useful for continuous testing

2. **Bookmark Points**
   - Save interesting timestamps
   - Jump to specific points

3. **Export Session**
   - Save replay session for later
   - Load previous sessions

4. **Multiple Speed Profiles**
   - Custom speed settings
   - Skip weekends/holidays

---

## 🔧 Implementation

### 1. Enhanced Local Data Service

**File**: `local_data_service.py` (modify existing)

```python
class LocalDataService:
    def __init__(self):
        self.mode = "LIVE"  # or "REPLAY"
        self.replay_config = {
            'start_time': None,
            'end_time': None,
            'speed': 1.0,
            'paused': False,
            'current_position': None
        }
    
    async def start_broadcasting(self):
        """Main broadcast loop"""
        if self.mode == "LIVE":
            await self.live_mode()
        elif self.mode == "REPLAY":
            await self.replay_mode()
    
    async def live_mode(self):
        """Current implementation - Angel One REST API"""
        while self.running:
            # Existing code...
            data = obj.getMarketData("FULL", ...)
            await self.broadcast_to_subscribers(data)
            await asyncio.sleep(5)
    
    async def replay_mode(self):
        """Replay historical data from database"""
        conn = sqlite3.connect('data/local_realtime.db')
        cursor = conn.cursor()
        
        # Get all records in time range
        cursor.execute("""
            SELECT symbol, ts, ltp, volume, oi, delta, gamma, theta, vega, iv
            FROM ltp_ticks
            WHERE ts BETWEEN ? AND ?
            ORDER BY ts
        """, (self.replay_config['start_time'], self.replay_config['end_time']))
        
        records = cursor.fetchall()
        total_records = len(records)
        
        current_ts = None
        batch = []
        
        for idx, record in enumerate(records):
            if self.replay_config['paused']:
                await asyncio.sleep(0.1)
                continue
            
            ts = record[1]
            
            # Group records by timestamp
            if current_ts is None:
                current_ts = ts
            
            if ts == current_ts:
                batch.append(record)
            else:
                # Broadcast batch for this timestamp
                await self.broadcast_batch(batch, current_ts)
                
                # Calculate sleep time based on speed
                sleep_time = 5.0 / self.replay_config['speed']
                await asyncio.sleep(sleep_time)
                
                # Start new batch
                batch = [record]
                current_ts = ts
                
                # Update progress
                self.replay_config['current_position'] = idx
                self.emit_progress(idx, total_records)
        
        # Broadcast final batch
        if batch:
            await self.broadcast_batch(batch, current_ts)
    
    async def broadcast_batch(self, records, timestamp):
        """Broadcast all records for a given timestamp"""
        for record in records:
            data = {
                'symbol': record[0],
                'ts': record[1],
                'ltp': record[2],
                'volume': record[3],
                'oi': record[4],
                'delta': record[5],
                'gamma': record[6],
                'theta': record[7],
                'vega': record[8],
                'iv': record[9],
                'source': 'replay'
            }
            await self.broadcast_to_subscribers(data)
```

---

### 2. Control Panel GUI

**File**: `broadcast_control_panel.py` (new)

```python
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
import threading
import asyncio

class BroadcastControlPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("📡 NIFTY Data Broadcast Control")
        self.root.geometry("600x700")
        
        self.service = None  # LocalDataService instance
        self.setup_ui()
    
    def setup_ui(self):
        # Mode Selection
        mode_frame = ttk.LabelFrame(self.root, text="Mode Selection", padding=10)
        mode_frame.pack(fill="x", padx=10, pady=5)
        
        self.mode_var = tk.StringVar(value="LIVE")
        ttk.Radiobutton(mode_frame, text="Live", variable=self.mode_var, 
                       value="LIVE", command=self.on_mode_change).pack(side="left")
        ttk.Radiobutton(mode_frame, text="Replay", variable=self.mode_var, 
                       value="REPLAY", command=self.on_mode_change).pack(side="left")
        
        # Replay Settings
        replay_frame = ttk.LabelFrame(self.root, text="Replay Settings", padding=10)
        replay_frame.pack(fill="x", padx=10, pady=5)
        
        # Date pickers
        ttk.Label(replay_frame, text="Start Date:").grid(row=0, column=0, sticky="w")
        self.start_date = DateTimePicker(replay_frame)
        self.start_date.grid(row=0, column=1)
        
        ttk.Label(replay_frame, text="End Date:").grid(row=1, column=0, sticky="w")
        self.end_date = DateTimePicker(replay_frame)
        self.end_date.grid(row=1, column=1)
        
        # Speed control
        ttk.Label(replay_frame, text="Speed:").grid(row=2, column=0, sticky="w")
        speed_frame = ttk.Frame(replay_frame)
        speed_frame.grid(row=2, column=1)
        
        self.speed_var = tk.DoubleVar(value=1.0)
        for speed in [1, 2, 5, 10]:
            ttk.Radiobutton(speed_frame, text=f"{speed}x", 
                           variable=self.speed_var, value=speed).pack(side="left")
        
        # Controls
        control_frame = ttk.LabelFrame(self.root, text="Controls", padding=10)
        control_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Button(control_frame, text="▶ Start", 
                  command=self.start_broadcast).pack(side="left", padx=5)
        ttk.Button(control_frame, text="⏸ Pause", 
                  command=self.pause_broadcast).pack(side="left", padx=5)
        ttk.Button(control_frame, text="⏹ Stop", 
                  command=self.stop_broadcast).pack(side="left", padx=5)
        
        # Status Display
        status_frame = ttk.LabelFrame(self.root, text="Status", padding=10)
        status_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.status_text = tk.Text(status_frame, height=10, state="disabled")
        self.status_text.pack(fill="both", expand=True)
        
        # Progress Bar
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=5)
    
    def start_broadcast(self):
        mode = self.mode_var.get()
        
        if mode == "REPLAY":
            start = self.start_date.get_datetime()
            end = self.end_date.get_datetime()
            speed = self.speed_var.get()
            
            # Configure replay
            self.service.set_replay_config(start, end, speed)
        
        # Start service in background thread
        self.service_thread = threading.Thread(
            target=self.run_service, 
            args=(mode,), 
            daemon=True
        )
        self.service_thread.start()
        
        self.update_status(f"Broadcasting in {mode} mode...")
    
    def run_service(self, mode):
        """Run service in background thread"""
        self.service.mode = mode
        asyncio.run(self.service.start_broadcasting())
    
    def update_status(self, message):
        self.status_text.config(state="normal")
        self.status_text.insert("end", f"[{datetime.now()}] {message}\n")
        self.status_text.config(state="disabled")
        self.status_text.see("end")

class DateTimePicker(ttk.Frame):
    """Custom datetime picker widget"""
    def __init__(self, parent):
        super().__init__(parent)
        
        # Date
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(self, textvariable=self.date_var, width=12).pack(side="left")
        
        # Time
        self.time_var = tk.StringVar(value="09:15")
        ttk.Entry(self, textvariable=self.time_var, width=6).pack(side="left", padx=5)
    
    def get_datetime(self):
        date_str = self.date_var.get()
        time_str = self.time_var.get()
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")

if __name__ == "__main__":
    panel = BroadcastControlPanel()
    panel.root.mainloop()
```

---

### 3. Integration

**File**: `start_with_gui.py` (new launcher)

```python
#!/usr/bin/env python3
"""
Launch Local Data Service with GUI Control Panel
"""

import sys
import threading
from local_data_service import LocalDataService
from broadcast_control_panel import BroadcastControlPanel

def main():
    # Create service instance
    service = LocalDataService()
    
    # Create GUI panel
    panel = BroadcastControlPanel()
    panel.service = service  # Link service to GUI
    
    # Start GUI (blocks until closed)
    panel.root.mainloop()

if __name__ == "__main__":
    main()
```

---

## 📊 Usage Scenarios

### Scenario 1: Test Strategy During Off-Hours

```
1. Open Control Panel
2. Select "Replay" mode
3. Choose date range: 2025-12-20 to 2025-12-21
4. Set speed: 2x (faster testing)
5. Click "Start"
6. Your trading projects receive data as if market is live!
```

### Scenario 2: Test Specific Market Conditions

```
1. Identify interesting date (e.g., high volatility day)
2. Set date range to that day
3. Set speed: 1x (realistic timing)
4. Test how your strategy performs
```

### Scenario 3: Continuous Testing

```
1. Select last week's data
2. Enable loop mode
3. Let strategies run overnight
4. Collect performance statistics
```

---

## ✅ Benefits

| Benefit | Description |
|---------|-------------|
| **No Code Changes** | Projects work as-is, just connect to WebSocket |
| **Realistic Testing** | Same timing as live (5-second intervals) |
| **24/7 Testing** | Test anytime, not just market hours |
| **Multiple Scenarios** | Test different dates, conditions |
| **Speed Control** | Test faster (10x) or realistic (1x) |
| **Safe** | No real trades, just testing |

---

## 🔄 VPS Sync Implementation

### Using Existing data_sync_service.py

We already have gap detection and filling logic in `services/data_sync_service.py`. Just need to integrate with GUI!

**Key Functions Available**:
- `detect_gap()` - Finds gaps in local database
- `fill_gap(start_ts, end_ts)` - Fills gap from VPS
- `check_and_fill_gaps_on_startup()` - Auto-check on startup

### GUI Integration Code

```python
# Add to BroadcastControlPanel class

def check_gaps(self):
    """Check for gaps in local database"""
    from services.data_sync_service import DataSyncService
    
    sync_service = DataSyncService()
    gap = sync_service.detect_gap()
    
    if gap:
        start_ts, end_ts = gap
        gap_msg = f"🔴 Gap: {start_ts} to {end_ts}"
        self.gap_status.config(text=gap_msg, foreground="red")
        
        # Ask if user wants to fill
        if messagebox.askyesno("Gap Detected", f"Fill gap now?"):
            self.sync_with_vps()
    else:
        self.gap_status.config(text="✅ No Gaps", foreground="green")

def sync_with_vps(self):
    """Sync with VPS to fill gaps"""
    threading.Thread(target=self._sync_thread, daemon=True).start()

def _sync_thread(self):
    """Background sync"""
    from services.data_sync_service import DataSyncService
    
    sync_service = DataSyncService()
    gap = sync_service.detect_gap()
    
    if gap:
        start_ts, end_ts = gap
        count = sync_service.fill_gap(start_ts, end_ts)
        self.update_status(f"✅ Added {count:,} records from VPS")
    else:
        self.update_status("✅ Already up-to-date")
```

### Auto-Sync Feature

```python
def toggle_auto_sync(self):
    """Enable periodic sync every 5 minutes"""
    if self.auto_sync_var.get():
        def auto_sync_loop():
            while self.auto_sync_var.get():
                time.sleep(300)  # 5 minutes
                self.sync_with_vps()
        
        threading.Thread(target=auto_sync_loop, daemon=True).start()
```

### Startup Gap Check

```python
async def startup_checks(self):
    """Auto-check gaps on startup"""
    from services.data_sync_service import DataSyncService
    
    sync_service = DataSyncService()
    sync_service.check_and_fill_gaps_on_startup()  # Existing function!
```

---

## 📋 Usage Workflows

### Morning Startup Workflow

```
1. Start GUI → Automatically checks for overnight gaps
2. If gap found → Prompts "Fill gap from 15:30 to 09:15?"
3. Click "Yes" → Syncs with VPS
4. ✅ Gap filled → Ready to broadcast
```

### Manual Sync Workflow

```
1. Click "Check Gaps" button
2. System shows: "🔴 Gap: 2026-01-02 12:00 - 14:30"
3. Click "Sync with VPS"
4. Status: "✅ Sync complete! Added 12,345 records"
```

### Auto-Sync Mode

```
1. Enable "Auto-Sync" checkbox
2. System syncs every 5 minutes automatically
3. Status updates: "Last Sync: 10:45 (123 records)"
```

---

## 🎯 Implementation Phases

### Phase 1: Core Replay (Essential)
- [  ] Modify `local_data_service.py` to support modes
- [  ] Implement `replay_mode()` function
- [  ] Basic mode switching (Live/Replay)
- [  ] Command-line control

### Phase 2: GUI Control Panel (Recommended)
- [  ] Create Tkinter GUI
- [  ] Date/time pickers
- [  ] Play/Pause/Stop controls
- [  ] Status display

### Phase 3: Advanced Features (Optional)
- [  ] Speed control (1x, 2x, 5x, 10x)
- [  ] Progress bar
- [  ] Symbol filtering
- [  ] Loop mode
- [  ] Bookmarks

---

## 🚀 Next Steps

Would you like me to:
1. ✅ Implement Phase 1 (Core Replay functionality)?
2. ✅ Create the Tkinter Control Panel?
3. ✅ Add advanced features?

This will enable **unlimited backtesting** with real market data! 🎯
