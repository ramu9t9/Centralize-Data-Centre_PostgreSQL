# 🎯 HANDOFF TO CURSOR - Real-Time Local Data Centre Implementation

## 📋 Executive Summary

**Objective**: Implement local real-time data centre that matches VPS behavior exactly - collecting NIFTY options with Greeks and IV using Angel One REST API.

**Architecture Reference**: See `realtime_architecture.md` for complete system design
- Original plan was WebSocket-based
- **Updated to REST API** to match VPS exactly
- REST API provides ALL fields (Greeks, IV) that WebSocket doesn't

**Current Status**: 90% Complete
- ✅ REST API polling infrastructure working (replaced WebSocket)
- ✅ Database schema correct
- ✅ Local WebSocket server for broadcasting to projects
- ✅ Gap filling and VPS sync implemented
- ⚠️ **Missing**: Options collection with Greeks/IV (currently only collecting NIFTY 50 index)

**What VPS Actually Does**:
- Collects **612 symbols** (NIFTY 50 + options)
- Uses **Angel One REST API** `getMarketData("FULL")` every 5 seconds (NOT WebSocket!)
- Collects **ATM ±5 strikes** (11 strikes × 2 options = 22 options + 1 index = 23 symbols)
- Gets **ALL fields**: LTP, Volume, OI, Delta, Gamma, Theta, Vega, IV

---

## 🏗️ Architecture Overview

### Data Flow (REST API Approach - Same as VPS):

**Reference**: See `realtime_architecture.md` → **Option 1: REST API Polling (RECOMMENDED - Same as VPS)**

```
Angel One REST API (1 login)
    ↓ getMarketData("FULL") every 5 seconds (Similar to VPS)
Local Data Service
    ↓ Broadcast via WebSocket
┌─────────┬─────────┬─────────┐
Project 1  Project 2  Project 3  (unlimited)
```

**Implementation**:
- Local service polls Angel One REST API every 5 seconds
- Gets ALL fields: LTP, Volume, OI, Delta, Gamma, Theta, Vega, IV
- Stores to `local_realtime.db`
- Broadcasts to projects via local WebSocket (ws://localhost:8765)
- Projects get updates every 5 seconds (same as VPS)
- VPS syncs every 5 minutes for gap filling

### Why REST API (Not WebSocket):

**WebSocket Limitations**:
- ❌ Binary data format (complex parsing)
- ❌ Only provides LTP, volume, OI
- ❌ **NO Greeks** (delta, gamma, theta, vega)
- ❌ **NO IV** (implied volatility)

**REST API Advantages** (VPS uses this):
- ✅ JSON format (easy parsing)
- ✅ Provides **ALL fields** including Greeks and IV
- ✅ Simple `getMarketData("FULL")` call
- ✅ Proven and working on VPS

**Note**: We still use a **local WebSocket server** (ws://localhost:8765) to broadcast data to your trading projects, but the data **source** is REST API, not Angel One WebSocket.

---

## 📚 Reference Documents

Before starting, review these documents for context:

1. **`realtime_architecture.md`** - Original architecture plan
   - Shows complete system design
   - WebSocket broadcasting to local projects
   - Gap filling and VPS sync strategy

2. **`vps_analysis.md`** - Critical findings
   - Explains why we switched from WebSocket to REST API
   - Shows what Angel One WebSocket provides vs. REST API
   - Proves VPS uses REST API, not WebSocket

3. **`ANGEL_WEBSOCKET_REALITY.md`** - WebSocket limitations
   - Documents what Angel One WebSocket actually provides
   - Explains missing fields (Greeks, IV)

**Key Insight**: The architecture plan in `realtime_architecture.md` is correct, we just replaced the Angel One WebSocket data source with REST API to match VPS behavior. Everything else (local WebSocket server, gap filling, VPS sync) remains the same.

---

## ✅ What's Already Working

### 1. Infrastructure (100% Complete)
- ✅ Angel One login with TOTP
- ✅ REST API polling every 5 seconds
- ✅ Database storage (`local_realtime.db`)
- ✅ WebSocket server (ws://localhost:8765)
- ✅ Client library (`realtime_client.py`)
- ✅ Gap filling service (`data_sync_service.py`)

### 2. Data Collection (50% Complete)
- ✅ NIFTY 50 index LTP collection working
- ✅ Timing perfect (5.0s intervals)
- ✅ No data gaps
- ⚠️ **Missing**: Options with Greeks/IV

---

## ⚠️ What Needs Implementation

### Critical Missing Feature: Options Collection with Greeks

**Current**: Only collecting NIFTY 50 index
**Required**: Collect ATM ±5 strikes with full Greeks and IV

**File to Edit**: `G:\Projects\Centralize Data Centre\local_data_service.py`

**Lines to Modify**: ~260-330 (the `start_angel_data_collection` function)

---

## 📁 VPS Reference Code

**VPS File**: `G:\Projects\OI Data Store in Cloud\nifty_stream_local_sqlite.py`

### Key Functions to Copy:

1. **`get_current_expiry()`** (Lines 446-518)
   - Finds next Thursday expiry
   - Handles expiry day scenarios

2. **`pick_watchlist()`** (Lines 644-684)
   - Gets NIFTY spot price
   - Calculates ATM strike
   - Selects ±5 strikes around ATM
   - Returns list of symbols to collect

3. **`fetch_market_data()`** (Lines 689-740)
   - Calls `obj.getMarketData("FULL", {"NFO": tokens})`
   - Handles both NSE (index) and NFO (options)
   - Returns all fields including Greeks

---

## 🔧 Implementation Steps

### Step 1: Copy VPS Helper Functions

Add these functions to `local_data_service.py` (before the `LocalDataService` class):

```python
def get_current_expiry(df, index_name='NIFTY'):
    """Get current month expiry - same logic as VPS"""
    # Copy from VPS lines 446-518
    # Returns expiry in format: "02JAN2026"
    pass

def get_spot_ltp(obj, nifty_token):
    """Get NIFTY spot price"""
    # Copy from VPS lines 607-642
    # Returns current NIFTY price
    pass

def pick_watchlist(obj, df, nifty_token, atm_window=5):
    """Select ATM ±5 strikes - same as VPS"""
    # Copy from VPS lines 644-684
    # Returns dict: {symbol: token}
    pass
```

### Step 2: Update `start_angel_data_collection()` Function

**Location**: `local_data_service.py`, line ~214

**Current Code** (lines 259-267):
```python
# For now, just collect NIFTY 50 index
symbols_to_collect = {
    'NIFTY 50': {'token': nifty_token, 'exchange': 'NSE'}
}
```

**Replace With**:
```python
# Get current expiry
current_expiry = get_current_expiry(df)
logger.info(f"Current expiry: {current_expiry}")

# Get NIFTY spot price
spot_ltp = get_spot_ltp(obj, nifty_token)
logger.info(f"NIFTY spot: {spot_ltp}")

# Calculate ATM and select watchlist
atm = int(round(spot_ltp / 50.0) * 50)
ATM_WINDOW = 5

# Filter options for current expiry
opt = df[(df['name'] == 'NIFTY') & (df['instrumenttype'] == 'OPTIDX')]
current_options = opt[opt['expiry'] == current_expiry]

# Build symbols_to_collect
symbols_to_collect = {
    'NIFTY 50': {'token': nifty_token, 'exchange': 'NSE'}
}

# Add ATM ±5 strikes
for offset in range(-ATM_WINDOW, ATM_WINDOW + 1):
    strike = atm + offset * 50
    
    # Find CE option
    ce = current_options[
        (current_options['strike'] == strike * 100) & 
        (current_options['symbol'].str.endswith('CE'))
    ]
    if not ce.empty:
        symbols_to_collect[ce.iloc[0]['symbol']] = {
            'token': str(ce.iloc[0]['token']),
            'exchange': 'NFO'
        }
    
    # Find PE option
    pe = current_options[
        (current_options['strike'] == strike * 100) & 
        (current_options['symbol'].str.endswith('PE'))
    ]
    if not pe.empty:
        symbols_to_collect[pe.iloc[0]['symbol']] = {
            'token': str(pe.iloc[0]['token']),
            'exchange': 'NFO'
        }

logger.info(f"Collecting {len(symbols_to_collect)} symbols (ATM: {atm}, ±{ATM_WINDOW} strikes)")
logger.info(f"Sample: {list(symbols_to_collect.keys())[:5]}")
```

### Step 3: Verify Data Transformation

**Location**: `local_data_service.py`, lines ~305-325

**Current Code** is correct, but verify it handles all fields:
```python
tick_data = {
    'symbol': symbol,
    'token': str(item.get('symbolToken', '')),
    'ts': datetime.now(timezone.utc).isoformat(),
    'ltp': item.get('ltp') or item.get('lastTradedPrice'),
    'bid': item.get('bestBidPrice'),
    'ask': item.get('bestAskPrice'),
    'volume': item.get('volumeTradeForTheDay') or item.get('volume'),
    'oi': item.get('openInterest'),
    'delta': item.get('delta'),      # ✅ Already there
    'gamma': item.get('gamma'),      # ✅ Already there
    'theta': item.get('theta'),      # ✅ Already there
    'vega': item.get('vega'),        # ✅ Already there
    'iv': item.get('iv') or item.get('impliedVolatility'),  # ✅ Already there
    'source': 'angel_api'
}
```

**This is already correct!** ✅

---

## 🧪 Testing & Verification

### Step 1: Start the Service

```powershell
cd "G:\Projects\Centralize Data Centre"
py local_data_service.py
```

**Expected Output**:
```
✅ Angel One login successful
Current expiry: 02JAN2026
NIFTY spot: 26287.20
Collecting 23 symbols (ATM: 26300, ±5 strikes)
Sample: ['NIFTY 50', 'NIFTY02JAN2626050CE', 'NIFTY02JAN2626050PE', ...]
Fetched 23 NFO records
Fetched 1 NSE records
```

### Step 2: Verify Database

```powershell
py check_vps_db.py
```

**Expected Output**:
```
Total unique symbols: 23
Option records (CE/PE): 100+
Sample records with Greeks:
  NIFTY02JAN2626250CE: LTP=150.5, Vol=5000, OI=50000, Delta=0.45, Gamma=0.002, Theta=-15.5, Vega=12.3, IV=15.2
```

### Step 3: Verify All Fields

Create verification script:
```python
import sqlite3
conn = sqlite3.connect('data/local_realtime.db')
cursor = conn.cursor()

# Check for options with Greeks
cursor.execute('''
    SELECT symbol, ltp, volume, oi, delta, gamma, theta, vega, iv 
    FROM ltp_ticks 
    WHERE source='angel_api' AND delta IS NOT NULL 
    ORDER BY ts DESC LIMIT 5
''')

print("Latest options with Greeks:")
for row in cursor.fetchall():
    print(f"  {row[0]}: LTP={row[1]}, Vol={row[2]}, OI={row[3]}")
    print(f"    Delta={row[4]}, Gamma={row[5]}, Theta={row[6]}, Vega={row[7]}, IV={row[8]}")

conn.close()
```

---

## 📊 Expected Results

### Before Fix (Current):
- Symbols: 1 (NIFTY 50 only)
- Greeks: NULL
- IV: NULL
- Volume: NULL
- OI: NULL

### After Fix (Target):
- Symbols: 23 (NIFTY 50 + 22 options)
- Greeks: ✅ Delta, Gamma, Theta, Vega
- IV: ✅ Implied Volatility
- Volume: ✅ Trading Volume
- OI: ✅ Open Interest

---

## 🎯 Success Criteria

1. ✅ Collecting 23 symbols (1 index + 22 options)
2. ✅ All options have Greeks (delta, gamma, theta, vega)
3. ✅ All options have IV (implied volatility)
4. ✅ All options have Volume and OI
5. ✅ Data updates every 5 seconds
6. ✅ No data gaps
7. ✅ Matches VPS database structure

---

## 🔍 Debugging Tips

### Issue: No options collected
**Check**: Expiry date calculation
**Fix**: Verify `current_expiry` format matches instrument list

### Issue: Greeks are NULL
**Check**: Angel One API response
**Fix**: Verify using `getMarketData("FULL")` not `getMarketData("LTP")`

### Issue: Strike prices wrong
**Check**: NIFTY spot price
**Fix**: Verify `get_spot_ltp()` returns correct value

---

## 📁 File References

### Files to Edit:
1. **`local_data_service.py`** - Main service (add options logic)

### Files Already Complete:
1. ✅ `realtime_client.py` - Client library
2. ✅ `data_sync_service.py` - Gap filling
3. ✅ `requirements_realtime.txt` - Dependencies
4. ✅ Database schema - Correct structure

### Reference Files (VPS):
1. **`G:\Projects\OI Data Store in Cloud\nifty_stream_local_sqlite.py`** - Copy logic from here

---

## 🚀 Quick Start for Cursor

1. **Open**: `G:\Projects\Centralize Data Centre\local_data_service.py`
2. **Reference**: `G:\Projects\OI Data Store in Cloud\nifty_stream_local_sqlite.py`
3. **Copy**: Functions `get_current_expiry()`, `get_spot_ltp()`, `pick_watchlist()`
4. **Update**: `start_angel_data_collection()` function (line ~214)
5. **Replace**: Lines 259-267 with ATM watchlist logic
6. **Test**: Run `py local_data_service.py`
7. **Verify**: Check database for options with Greeks

---

## ✅ Final Checklist

- [ ] Copy VPS helper functions
- [ ] Update `start_angel_data_collection()` with ATM logic
- [ ] Test service starts without errors
- [ ] Verify 23 symbols being collected
- [ ] Confirm Greeks and IV in database
- [ ] Check data updates every 5 seconds
- [ ] Verify no data gaps
- [ ] Compare with VPS database structure

---

## 📞 Support

**VPS Database**: `G:\Projects\Centralize Data Centre\data\nifty_local.db`
- Contains 612 symbols with full Greeks/IV
- Use as reference for expected output

**Current Local DB**: `G:\Projects\Centralize Data Centre\data\local_realtime.db`
- Currently has only NIFTY 50 index
- Should match VPS after fix

---

**Status**: Ready for Cursor implementation
**Estimated Time**: 30-60 minutes
**Complexity**: Medium (mostly copy-paste from VPS)
**Risk**: Low (VPS code is proven and working)

---

**Last Updated**: January 2, 2026 12:12 PM IST
