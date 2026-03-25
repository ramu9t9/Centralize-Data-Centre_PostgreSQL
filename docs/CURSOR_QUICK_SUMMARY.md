# Quick Summary - For Cursor Implementation

## What's Missing: Options Collection with Greeks/IV

**Current**: Only collecting NIFTY 50 index (no Greeks/IV)
**Required**: Collect ATM ±5 strikes with full Greeks and IV (like VPS does)

## VPS Collects:
- 612 symbols (NIFTY 50 + options)
- ATM ±5 strikes = 23 symbols total
- Full Greeks: Delta, Gamma, Theta, Vega, IV
- Volume, OI, LTP

## File to Edit:
`G:\Projects\Centralize Data Centre\local_data_service.py`

## What to Copy from VPS:
File: `G:\Projects\OI Data Store in Cloud\nifty_stream_local_sqlite.py`

Functions to copy:
1. `get_current_expiry()` (lines 446-518)
2. `get_spot_ltp()` (lines 607-642)  
3. `pick_watchlist()` (lines 644-684)

## Where to Update:
Function: `start_angel_data_collection()` (line ~214)
Replace lines 259-267 with ATM watchlist logic

## Expected Result:
- 23 symbols collected (1 index + 22 options)
- All options have Greeks and IV
- Data updates every 5 seconds
- Matches VPS exactly

## Verification:
```python
# Check database
py check_vps_db.py

# Should show:
# - 23 symbols
# - Options with Delta, Gamma, Theta, Vega, IV
# - Volume and OI populated
```

See `CURSOR_HANDOFF.md` for complete details.
