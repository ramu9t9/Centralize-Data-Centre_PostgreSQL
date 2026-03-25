# 📊 NIFTY Database Schema Reference

**Complete database schema and usage guide for external projects**

**Last Updated:** February 2026  
**Database:** PostgreSQL (connection via `DATABASE_URL` environment variable)  
**Database Type:** PostgreSQL 14+  
**Timezone:** All connections use **IST (Asia/Kolkata)**. Timestamps are stored as TIMESTAMPTZ (UTC instants); they display and aggregate in IST.

---

## 📋 Table of Contents

1. [Database Overview](#database-overview)
2. [Table Schema](#table-schema)
3. [Data Types & Fields](#data-types--fields)
4. [Indexes](#indexes)
5. [Data Coverage](#data-coverage)
6. [Sample Queries](#sample-queries)
7. [Connection Examples](#connection-examples)
8. [Best Practices](#best-practices)

---

## 📊 Database Overview

### Purpose
Centralized PostgreSQL database containing NIFTY 50 options, futures, and index data with real-time market data including prices, volume, open interest, and option Greeks. Data is synced from VPS (SQLite) to local PostgreSQL.

### Data Range
- **Start Date:** August 29, 2025 (09:15:00 IST)
- **End Date:** Current (ongoing)
- **Update Frequency:** Incremental sync from VPS (recommended daily)

### Data Collection
- **Source:** Angel One API via VPS server
- **Collection Frequency:** Every 5 seconds during market hours
- **Market Hours:** 09:15:00 - 15:30:00 IST (03:45:00 - 10:00:00 UTC)

---

## 📐 Table Schema

### Main Table: `ltp_ticks`

The primary table containing all market data records (PostgreSQL schema).

```sql
CREATE TABLE ltp_ticks (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(64) NOT NULL,
    token VARCHAR(32) NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    ltp DOUBLE PRECISION,
    bid DOUBLE PRECISION,
    ask DOUBLE PRECISION,
    volume BIGINT,
    oi BIGINT,
    delta DOUBLE PRECISION,
    gamma DOUBLE PRECISION,
    theta DOUBLE PRECISION,
    vega DOUBLE PRECISION,
    iv DOUBLE PRECISION DEFAULT 0.0,
    source VARCHAR(16) DEFAULT 'ws',
    UNIQUE(symbol, ts)
);
```

### Constraints
- **Primary Key:** `id` (auto-increment)
- **Unique Constraint:** `(symbol, ts)` - prevents duplicate records
- **NOT NULL:** `symbol`, `token`, `ts`

### 1-minute OHLC table: `ohlc_1min`

One row per (symbol, minute). Table is created by schema init; no build scripts in this project. Populate from `ltp_ticks` with your own process if needed.

```sql
CREATE TABLE ohlc_1min (
    symbol VARCHAR(64) NOT NULL,
    ts TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT DEFAULT 0,
    oi BIGINT,
    delta DOUBLE PRECISION,
    iv DOUBLE PRECISION,
    UNIQUE(symbol, ts)
);
```

| Column  | Type                         | Description                |
|---------|------------------------------|----------------------------|
| symbol  | VARCHAR(64) NOT NULL         | Instrument symbol          |
| ts      | TIMESTAMP WITHOUT TIME ZONE  | Minute bucket              |
| open    | DOUBLE PRECISION             | First LTP in minute        |
| high    | DOUBLE PRECISION             | Max LTP in minute          |
| low     | DOUBLE PRECISION             | Min LTP in minute          |
| close   | DOUBLE PRECISION             | Last LTP in minute         |
| volume  | BIGINT DEFAULT 0             | Sum of volume in minute    |
| oi      | BIGINT                       | OI at minute end           |
| delta   | DOUBLE PRECISION             | Delta at minute end        |
| iv      | DOUBLE PRECISION             | IV at minute end           |

Indexes: `idx_ohlc_1min_symbol_ts`, `idx_ohlc_1min_ts`, `idx_ohlc_1min_symbol`.

---

### Backtest view: `v_ltp_ticks_backtest`

Single-table view for backtesting: one row per (symbol, ts) with **Date(IST)** and **Time(IST)** plus all metrics (LTP, Volume, OI, Delta, Gamma, Theta, Vega, IV). Same layout as "Latest Records from LTP_TICKS".

| Column    | Type   | Description                    |
|-----------|--------|--------------------------------|
| date_ist  | date   | Trading date in IST            |
| time_ist  | text   | Time in IST (HH24:MI:SS)       |
| symbol    | varchar| Instrument symbol              |
| ltp       | float  | Last traded price              |
| volume    | bigint | Volume                         |
| oi        | bigint | Open interest                  |
| delta     | float  | Delta                          |
| gamma     | float  | Gamma                          |
| theta     | float  | Theta                          |
| vega      | float  | Vega                           |
| iv        | float  | Implied volatility             |
| ts        | timestamptz | Original UTC timestamp   |
| token, bid, ask, source | ... | Extra fields from base table |

**Example – all instruments at one timestamp (snapshot like your image):**
```sql
SELECT date_ist, time_ist, symbol, ltp, volume, oi, delta, gamma, theta, vega, iv
FROM v_ltp_ticks_backtest
WHERE ts = '2026-02-25T09:43:00+00:00'
ORDER BY symbol;
```

**Example – one day for backtesting:**
```sql
SELECT * FROM v_ltp_ticks_backtest
WHERE date_ist = '2026-02-25'
ORDER BY ts, symbol;
```

### Additional Tables

#### `oi_snapshots`
Open Interest snapshot table (internal use, may be empty or used for historical OI tracking).

```sql
CREATE TABLE oi_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    token TEXT NOT NULL,
    ts TEXT NOT NULL,
    oi INTEGER,
    volume INTEGER,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    iv REAL DEFAULT 0.0
);
```

#### `oi_snapshots_archive`
Archived Open Interest snapshots (internal use).

```sql
CREATE TABLE oi_snapshots_archive (
    id INT,
    symbol TEXT,
    token TEXT,
    ts TEXT,
    oi INT,
    volume INT,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL
);
```

**Note:** These tables are primarily for internal system use. Most queries should focus on the `ltp_ticks` table.

---

## 📝 Data Types & Fields

### Symbol Types

#### 1. Index
- **Pattern:** `"NIFTY 50"`
- **Example:** `"NIFTY 50"`
- **Volume:** Always 0 (indices don't have volume)
- **IV/Greeks:** NULL (not applicable)

#### 2. Options
- **Call Options Pattern:** `"NIFTY{EXPIRY}{STRIKE}CE"`
- **Put Options Pattern:** `"NIFTY{EXPIRY}{STRIKE}PE"`
- **Example Call:** `"NIFTY25NOV2526000CE"` (NIFTY, 25 Nov 2025, Strike 26000, Call)
- **Example Put:** `"NIFTY25NOV2526000PE"` (NIFTY, 25 Nov 2025, Strike 26000, Put)
- **Volume:** Available (95%+ coverage)
- **IV/Greeks:** Available (95%+ coverage from Aug 29, 2025)

#### 3. Futures
- **Pattern:** `"NIFTY{EXPIRY}FUT"`
- **Example:** `"NIFTY28NOV25FUT"` (NIFTY, 28 Nov 2025, Future)
- **Volume:** Available (98%+ coverage)
- **IV/Greeks:** NULL (futures don't have IV)

### Field Descriptions

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `id` | INTEGER | Auto-increment primary key | `1234567` | Unique identifier |
| `symbol` | TEXT | Trading symbol | `"NIFTY25NOV2526000CE"` | NOT NULL |
| `token` | TEXT | Angel One token ID | `"12345"` | NOT NULL |
| `ts` | TEXT | Timestamp (ISO 8601, UTC, canonical) | `"2025-12-30T09:15:00"` or `"2025-09-09T08:27:12.274533"` | NOT NULL, indexed |
| `ltp` | REAL | Last Traded Price | `125.50` | Can be NULL |
| `bid` | REAL | Best Bid Price | `125.25` | Can be NULL |
| `ask` | REAL | Best Ask Price | `125.75` | Can be NULL |
| `volume` | INTEGER | Trading Volume | `1500000` | 0 for indices, NULL possible |
| `oi` | INTEGER | Open Interest | `5000000` | Options/Futures only |
| `delta` | REAL | Option Delta | `0.5234` | Options only, -1 to 1 |
| `gamma` | REAL | Option Gamma | `0.0012` | Options only |
| `theta` | REAL | Option Theta | `-0.0234` | Options only, usually negative |
| `vega` | REAL | Option Vega | `0.1234` | Options only |
| `iv` | REAL | Implied Volatility (%) | `15.25` | Options only, percentage |
| `source` | TEXT | Data source | `"ws"` or `"api"` | Default: 'ws' |

### Timestamp Format
- **Format:** ISO 8601 canonical UTC (without timezone suffix)
- **Examples:** 
  - `"2025-12-30T09:15:00"` (seconds precision)
  - `"2025-09-09T08:27:12.274533"` (with microseconds)
- **Timezone:** UTC (always, implicit - no `+00:00` suffix)
- **Precision:** Seconds or microseconds (varies by record)

---

## 🔍 Indexes

For optimal query performance, the following indexes are created:

```sql
-- Composite index for symbol + timestamp queries
CREATE INDEX idx_ticks_symbol_ts ON ltp_ticks(symbol, ts);

-- Timestamp index for time-range queries
CREATE INDEX idx_ticks_ts ON ltp_ticks(ts);

-- Symbol index for symbol-based queries
CREATE INDEX idx_ticks_symbol ON ltp_ticks(symbol);

-- Unique constraint to prevent duplicates
CREATE UNIQUE INDEX ux_ltp_symbol_ts ON ltp_ticks(symbol, ts);
```

### Index Usage Recommendations
- Use `(symbol, ts)` for symbol-specific time-series queries
- Use `ts` for time-range queries across all symbols
- Use `symbol` for filtering by symbol type

---

## 📈 Data Coverage

### Volume Data
- **Start Date:** August 29, 2025
- **Coverage:** 100% for Options and Futures from Aug 29 onwards
- **Index:** Always 0 (expected)

### IV (Implied Volatility) Data
- **Start Date:** August 29, 2025
- **Coverage:** 95%+ for Options from Aug 29 onwards
- **Futures/Index:** NULL (not applicable)

### Greeks Data (Delta, Gamma, Theta, Vega)
- **Start Date:** August 29, 2025
- **Coverage:** 95%+ for Options from Aug 29 onwards
- **Futures/Index:** NULL (not applicable)

### Monthly Statistics (as of Dec 2025)
- **August 2025:** Volume 100%, IV 99.7%
- **September 2025:** Volume 100%, IV 95.3%
- **October 2025:** Volume 100%, IV 97.4%
- **November 2025:** Volume 100%, IV 97.2%
- **December 2025:** Volume 100%, IV 95.0%

---

## 💻 Sample Queries

### Basic Queries

#### Get Latest Records
```sql
SELECT * FROM ltp_ticks 
ORDER BY ts DESC 
LIMIT 100;
```

#### Get Records for Specific Symbol
```sql
SELECT * FROM ltp_ticks 
WHERE symbol = 'NIFTY25NOV2526000CE'
ORDER BY ts DESC;
```

#### Get Records for Date Range
```sql
SELECT * FROM ltp_ticks 
WHERE ts >= '2025-12-30T03:45:00' 
  AND ts < '2025-12-31T00:00:00'
ORDER BY ts;
```

### Options-Specific Queries

#### Get Options with Full Greeks
```sql
SELECT symbol, ts, ltp, volume, oi, delta, gamma, theta, vega, iv
FROM ltp_ticks 
WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
  AND symbol != 'NIFTY 50'
  AND delta IS NOT NULL
  AND iv IS NOT NULL
  AND iv > 0
ORDER BY ts DESC
LIMIT 100;
```

#### Get Options by Strike Range
```sql
SELECT * FROM ltp_ticks
WHERE symbol LIKE 'NIFTY%26000%'  -- Strike 26000
  AND (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
ORDER BY ts DESC;
```

#### Get Options by Expiry
```sql
SELECT * FROM ltp_ticks
WHERE symbol LIKE '%25NOV25%'  -- Expiry: 25 Nov 2025
  AND (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
ORDER BY ts DESC;
```

### Volume and OI Queries

#### Get High Volume Options
```sql
SELECT symbol, ts, volume, oi, ltp
FROM ltp_ticks
WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
  AND volume > 1000000
ORDER BY volume DESC
LIMIT 50;
```

#### Get Options with High Open Interest
```sql
SELECT symbol, ts, oi, volume, ltp
FROM ltp_ticks
WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
  AND oi > 10000000
ORDER BY oi DESC
LIMIT 50;
```

### Time-Series Queries

#### Get NIFTY 50 Index Data
```sql
SELECT ts, ltp, bid, ask
FROM ltp_ticks
WHERE symbol = 'NIFTY 50'
  AND ts >= '2025-12-30T03:45:00'
ORDER BY ts;
```

#### Get Hourly Aggregates
```sql
SELECT 
    strftime('%Y-%m-%d %H:00', ts) as hour,
    symbol,
    AVG(ltp) as avg_price,
    MAX(volume) as max_volume,
    SUM(volume) as total_volume
FROM ltp_ticks
WHERE ts >= '2025-12-30T03:45:00'
  AND (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
GROUP BY hour, symbol
ORDER BY hour, symbol;
```

### Statistical Queries

#### Get Database Statistics
```sql
SELECT 
    COUNT(*) as total_records,
    COUNT(DISTINCT symbol) as unique_symbols,
    MIN(ts) as earliest_record,
    MAX(ts) as latest_record,
    COUNT(CASE WHEN volume > 0 THEN 1 END) as records_with_volume,
    COUNT(CASE WHEN iv > 0 THEN 1 END) as records_with_iv
FROM ltp_ticks;
```

#### Get Symbol Type Counts
```sql
SELECT 
    CASE 
        WHEN symbol = 'NIFTY 50' THEN 'Index'
        WHEN symbol LIKE '%FUT%' THEN 'Futures'
        WHEN symbol LIKE '%CE%' OR symbol LIKE '%PE%' THEN 'Options'
        ELSE 'Other'
    END as symbol_type,
    COUNT(*) as count
FROM ltp_ticks
GROUP BY symbol_type;
```

---

## 🔌 Connection Examples

### Python (PostgreSQL / psycopg2)

```python
import os
import psycopg2

# Connect using DATABASE_URL (e.g. postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data)
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cursor = conn.cursor()

# Execute query (use %s placeholders)
cursor.execute("SELECT * FROM ltp_ticks WHERE symbol = %s ORDER BY ts DESC LIMIT 10", 
               ("NIFTY 50",))
rows = cursor.fetchall()

conn.close()
```

### Python (pandas)

```python
import os
import pandas as pd
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])

df = pd.read_sql_query("""
    SELECT * FROM ltp_ticks 
    WHERE ts >= '2025-12-30T03:45:00'::timestamptz
    ORDER BY ts
""", conn)
df['ts'] = pd.to_datetime(df['ts'])
conn.close()
```

### Python (SQLAlchemy)

```python
from sqlalchemy import create_engine
import pandas as pd
import os

engine = create_engine(os.environ["DATABASE_URL"])
df = pd.read_sql("SELECT * FROM ltp_ticks LIMIT 100", engine)
```

### R

```r
library(RSQLite)
library(DBI)

# Connect
con <- dbConnect(RSQLite::SQLite(), 
                 "G:/Projects/Centralize Data Centre/data/nifty_local.db")

# Query
df <- dbGetQuery(con, "SELECT * FROM ltp_ticks LIMIT 100")

# Close
dbDisconnect(con)
```

### Node.js

```javascript
const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const dbPath = path.join('G:', 'Projects', 'Centralize Data Centre', 'data', 'nifty_local.db');
const db = new sqlite3.Database(dbPath);

db.all("SELECT * FROM ltp_ticks LIMIT 100", (err, rows) => {
    if (err) {
        console.error(err);
    } else {
        console.log(rows);
    }
    db.close();
});
```

---

## ✅ Best Practices

### 1. Connection Management
- Always close database connections after use
- Use connection pooling for multiple queries
- Use context managers (Python `with` statement) when possible

### 2. Query Optimization
- Use indexes: Filter by `symbol` and `ts` together when possible
- Limit result sets: Use `LIMIT` for large queries
- Use specific columns: Select only needed columns, not `SELECT *`

### 3. Date/Time Handling
- Always use UTC timestamps in queries (stored without timezone suffix)
- Convert to local timezone (IST) for display only
- Use ISO 8601 canonical format: `YYYY-MM-DDTHH:MM:SS` or `YYYY-MM-DDTHH:MM:SS.ffffff`
- **Important:** Do not include `+00:00` suffix in queries - timestamps are stored in canonical UTC format

### 4. Data Validation
- Check for NULL values before calculations
- Validate symbol patterns before filtering
- Handle missing IV/Greeks data gracefully

### 5. Performance Tips
- Use prepared statements for repeated queries
- Batch insert operations when adding data
- Use transactions for multiple operations
- Consider read-only connections for queries

### 6. Common Patterns

#### Get Latest Data for Symbol
```sql
SELECT * FROM ltp_ticks 
WHERE symbol = ? 
ORDER BY ts DESC 
LIMIT 1;
```

#### Get Data for Specific Date
```sql
SELECT * FROM ltp_ticks 
WHERE DATE(ts) = '2025-12-30'
ORDER BY ts;
```

#### Get Options Chain for Strike
```sql
SELECT * FROM ltp_ticks
WHERE symbol LIKE 'NIFTY%26000%'
  AND (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
  AND ts >= '2025-12-30T03:45:00'
ORDER BY symbol, ts;
```

---

## 📚 Additional Resources

### Related Documentation
- **VPS_DATA_FETCH_COMPLETE_GUIDE.md** - Complete guide for fetching data from VPS
- **README.md** - Project overview and sync instructions
- **Important_command.md** - Quick reference for common commands

### Database Connection
Set the `DATABASE_URL` environment variable (e.g. in `.env`). Default server: Host localhost, Port 5432, User nifty_app, Database Centralized_Index_Option_Data:
```
DATABASE_URL=postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data
```

### Sync Information
- **Sync Script:** `sync_nifty_db.py` or `sync_nifty_db.ps1`
- **Sync Frequency:** Recommended daily after market close
- **Backup Location:** `G:\Projects\Centralize Data Centre\data\backups\`

---

## 🔄 Data Updates

### Sync Process
1. Run sync script: `.\sync_nifty_db.ps1`
2. Script automatically:
   - Creates backup
   - Fetches only new records (incremental)
   - Updates volume data if available
   - Verifies database integrity

### Checking for Updates
```python
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cursor = conn.cursor()
cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
latest = cursor.fetchone()[0]
print(f"Latest data: {latest}")
conn.close()
```

---

## ⚠️ Important Notes

1. **Read-Only Access Recommended:** The database is synced from VPS. Avoid direct modifications.

2. **Timestamp Format:** All timestamps are stored in canonical UTC format (without `+00:00` suffix), e.g., `"2025-12-30T09:15:00"` or `"2025-09-09T08:27:12.274533"`. Convert to IST (UTC+5:30) for display.

3. **NULL Values:** 
   - Volume = 0 for indices (expected)
   - IV/Greeks = NULL for futures and indices (expected)
   - Some options may have NULL IV (illiquid options)

4. **Data Completeness:**
   - Volume data: 100% from Aug 29, 2025
   - IV data: 95%+ from Aug 29, 2025
   - Pre-Aug 29 data was removed (incomplete)

5. **Symbol Naming:**
   - Expiry format: `DDMMMYY` (e.g., `25NOV25`)
   - Strike: 4-5 digits (e.g., `26000`)
   - Type: `CE` (Call) or `PE` (Put)

---

## 📞 Support

For issues or questions:
1. Check sync logs: `data\sync_log.txt`
2. Verify database integrity
3. Check VPS connection
4. Review backup files if needed

---

**Last Updated:** January 9, 2026  
**Version:** 1.0.1  
**Status:** Production Ready

