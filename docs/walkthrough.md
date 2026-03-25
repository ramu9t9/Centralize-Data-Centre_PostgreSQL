# Technical Handover: Sync System Fixes

## 1. The "Smoking Gun" Issues

### A. Data Sync Failure (The 32k Record Gap)
**The Issue:** The local DB was missing ~32k records. The sync script ran but found "0 missing records".
**The Smoking Gun:** **Timestamp Format Mismatch**.
- **VPS DB**: Stored timestamps as `YYYY-MM-DDTHH:MM:SS+00:00` (ISO with TZ).
- **Local Script**: Queried using exact string match `WHERE ts = 'YYYY-MM-DDTHH:MM:SS'`.
- **Result**: `SELECT ... WHERE ts = '...T10:00:00'` matched nothing against `'...T10:00:00+00:00'`.

**The Solution:**
- Updated SQL queries to use `LIKE 'timestamp%'` pattern matching.
- Applied to all fetch methods: `batch_find_missing_symbols_for_timestamps`, `fetch_rows_for_timestamp_and_symbols`.

---

### B. Sync Performance (The "Forever" Loop)
**The Issue:** Syncing a single day took hours/timeout.
**The Smoking Gun:** **Per-Timestamp Fetching (N+1 Problem)**.
- The gap detection found 4,482 broken timestamps.
- The script executed **4,482 separate SSH commands** to fetch them.
- **Result**: Massive overhead from SSH handshakes.

**The Solution:**
- Implemented **Range-Based Sync** (`fetch_vps_rows_by_range`).
- Uses `WHERE ts >= start AND ts <= end`.
- Fetches data in **1-hour chunks** (bulk).
- Added heuristic: If gap > 120s, use Range Sync (Fast). If < 120s, use Precision Sync.
- **Impact**: Reduced 4,482 SSH calls to ~8 calls. Time reduced from ~67 mins to ~8 mins.

---

### C. GUI Silent Failures ("Nothing is Happening")
**The Issue:** Clicking "Check Gaps" resulted in no action, no logs, or "Unknown Error".
**The Smoking Gun(s):**
1.  **Unicode Crash**: The script prints emojis (❌, ✅). Python on Windows subprocess defaults to `cp1252` encoding, crashing on emojis.
2.  **Thread Safety Violation**: The background thread tried to read `self.range_var.get()` (Tkinter widget). Tkinter is **not thread-safe**; accessing widgets from background threads causes silent freezes/crashes.
3.  **Blocking IO**: Used `subprocess.run()` which waits for the *entire* process (minutes) before showing any logs, making it look stuck.

**The Solution:**
1.  **Environment**: Set `PYTHONIOENCODING=utf-8` in subprocess environment.
2.  **Thread Safety**: Moved all UI reads (`.get()`) to the **Main Thread**, then passed values as arguments to the background thread.
3.  **Streaming**: switched to `subprocess.Popen` with `stdout.readline()` to stream logs in real-time to the GUI.

---

## 2. Key Code Changes

### `services/sync_nifty_db.py`
- **Added `--check-gaps` CLI**: Outputs JSON for GUI consumption.
- **Optimized Fetching**:
  ```python
  # OLD (Slow)
  for ts in timestamps: fetch_one(ts)
  
  # NEW (Fast)
  if gap > 120s: fetch_range(start, end) # Uses >= and <=
  else: fetch_specific(timestamps)
  ```

### `services/broadcast_control_panel.py`
- **Refactored `check_gaps`**:
  - Validates script path before running.
  - Captures UI values in main thread.
  - Streams subprocess output line-by-line.
  - parses JSON result for clean UI status.

## 3. Verification
- **Backend Test**: Ran `verify_fix.py` to confirm subprocess/encoding fix works in isolation.
- **Performance Test**: Filled 32k gap (107k records) in ~8 minutes.
- **Gap Check**: Verified 0 gaps remain after sync.
