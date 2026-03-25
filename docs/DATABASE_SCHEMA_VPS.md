# NIFTY Data Collection Database Schema

**Database:** `nifty_local.db`  
**Location:** `/opt/nifty-data-collector/data/nifty_local.db`  
**Database Type:** SQLite 3  
**Last Updated:** January 3, 2026

---

## 📊 Database Overview

| Metric | Value |
|--------|-------|
| **Total Tables** | 3 |
| **Total Records (ltp_ticks)** | 8,646,168 |
| **Total Records (oi_snapshots)** | 0 |
| **Total Records (oi_snapshots_archive)** | 7,021 |
| **Unique Symbols** | 637 |
| **Data Range** | 2025-08-29 to 2026-01-02 |
| **Database Size** | ~2.85 GB |

---

## 📋 Table: `ltp_ticks`

**Purpose:** Stores real-time LTP (Last Traded Price) data for NIFTY options, futures, and spot with every 5 seconds of tick data.

### Schema

```sql
CREATE TABLE ltp_ticks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  token  TEXT NOT NULL,
  ts TEXT NOT NULL,
  ltp REAL,
  bid REAL,
  ask REAL,
  volume INTEGER,
  oi INTEGER,
  delta REAL,
  gamma REAL,
  theta REAL,
  vega REAL,
  source TEXT DEFAULT 'ws',
  iv REAL DEFAULT 0.0
);
```

### Column Details

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Primary key, auto-increment |
| `symbol` | TEXT | NO | - | Trading symbol (e.g., "NIFTY06JAN2626250CE") |
| `token` | TEXT | NO | - | Instrument token from broker API |
| `ts` | TEXT | NO | - | Timestamp (ISO 8601 format, UTC) |
| `ltp` | REAL | YES | NULL | Last Traded Price |
| `bid` | REAL | YES | NULL | Best Bid Price |
| `ask` | REAL | YES | NULL | Best Ask Price |
| `volume` | INTEGER | YES | NULL | Trading Volume |
| `oi` | INTEGER | YES | NULL | Open Interest |
| `delta` | REAL | YES | NULL | Options Greek: Delta |
| `gamma` | REAL | YES | NULL | Options Greek: Gamma |
| `theta` | REAL | YES | NULL | Options Greek: Theta |
| `vega` | REAL | YES | NULL | Options Greek: Vega |
| `source` | TEXT | YES | 'ws' | Data source (default: 'ws' for WebSocket) |
| `iv` | REAL | YES | 0.0 | Implied Volatility |

### Indexes

| Index Name | Type | Columns | Purpose |
|------------|------|---------|---------|
| `idx_ticks_symbol_ts` | INDEX | (symbol, ts) | Fast lookups by symbol and time |
| `idx_ticks_ts` | INDEX | (ts) | Fast time-based queries |
| `idx_ticks_symbol` | INDEX | (symbol) | Fast symbol-based queries |
| `idx_ticks_symbol_ts_unique` | UNIQUE | (symbol, ts) | **Prevents duplicates** |
| `ux_ltp_symbol_ts` | UNIQUE | (symbol, ts) | **UPSERT constraint** |

### Constraints

- **UNIQUE Constraint:** `(symbol, ts)` - Ensures no duplicate records for the same symbol at the same timestamp
- **UPSERT Logic:** Uses `ON CONFLICT(symbol, ts) DO UPDATE` to update existing records instead of creating duplicates

### Sample Data

```
id: 1
symbol: "NIFTY06JAN2626250CE"
token: "12345"
ts: "2026-01-02T09:15:00+00:00"
ltp: 44.4
bid: 44.0
ask: 44.8
volume: 12500
oi: 150000
delta: 0.4520
gamma: 0.0005
theta: -33.2339
vega: 12.2255
source: "ws"
iv: 5.5600
```

---

## 📋 Table: `oi_snapshots`

**Purpose:** Stores Open Interest (OI) snapshots (currently not actively used).

### Schema

```sql
CREATE TABLE oi_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  token  TEXT NOT NULL,
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

### Column Details

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | NO | AUTO | Primary key, auto-increment |
| `symbol` | TEXT | NO | - | Trading symbol |
| `token` | TEXT | NO | - | Instrument token |
| `ts` | TEXT | NO | - | Timestamp (ISO 8601 format, UTC) |
| `oi` | INTEGER | YES | NULL | Open Interest |
| `volume` | INTEGER | YES | NULL | Trading Volume |
| `delta` | REAL | YES | NULL | Options Greek: Delta |
| `gamma` | REAL | YES | NULL | Options Greek: Gamma |
| `theta` | REAL | YES | NULL | Options Greek: Theta |
| `vega` | REAL | YES | NULL | Options Greek: Vega |
| `iv` | REAL | YES | 0.0 | Implied Volatility |

### Indexes

| Index Name | Type | Columns | Purpose |
|------------|------|---------|---------|
| `ux_oi_symbol_ts` | UNIQUE | (symbol, ts) | **UPSERT constraint** |

### Constraints

- **UNIQUE Constraint:** `(symbol, ts)` - Prevents duplicate OI snapshots

---

## 📋 Table: `oi_snapshots_archive`

**Purpose:** Archive table for old OI snapshot data.

### Schema

```sql
CREATE TABLE oi_snapshots_archive(
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

### Column Details

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | INT | YES | Record ID (no auto-increment) |
| `symbol` | TEXT | YES | Trading symbol |
| `token` | TEXT | YES | Instrument token |
| `ts` | TEXT | YES | Timestamp |
| `oi` | INT | YES | Open Interest |
| `volume` | INT | YES | Trading Volume |
| `delta` | REAL | YES | Options Greek: Delta |
| `gamma` | REAL | YES | Options Greek: Gamma |
| `theta` | REAL | YES | Options Greek: Theta |
| `vega` | REAL | YES | Options Greek: Vega |

**Note:** This table has no primary key or unique constraints. It's used for historical data storage.

---

## 🔑 Key Features

### 1. UPSERT Mechanism

The database uses SQLite's `ON CONFLICT` clause to implement UPSERT (INSERT or UPDATE):

```sql
INSERT INTO ltp_ticks (symbol, ts, ltp, ...) 
VALUES (?, ?, ?, ...)
ON CONFLICT(symbol, ts) 
DO UPDATE SET 
  ltp = excluded.ltp,
  bid = excluded.bid,
  ask = excluded.ask,
  volume = excluded.volume,
  oi = excluded.oi,
  delta = excluded.delta,
  gamma = excluded.gamma,
  theta = excluded.theta,
  vega = excluded.vega,
  iv = excluded.iv
```

**Benefits:**
- Prevents duplicate records
- Updates existing records with latest data
- Handles service restarts gracefully
- Maintains data integrity

### 2. Data Collection Pattern

- **Collection Interval:** Every 5 seconds during market hours
- **Symbols Tracked:** ~24 symbols per collection (1 spot + 1 futures + 22 options)
- **Data Points per Day:** ~107,900 records per trading day
- **Unique Constraint:** Ensures exactly one record per (symbol, timestamp) combination

### 3. Timestamp Format

- **Format:** ISO 8601 with timezone (UTC)
- **Example:** `2026-01-02T09:15:00+00:00`
- **Precision:** Microseconds supported

### 4. Options Greeks

All options symbols include:
- **Delta:** Price sensitivity to underlying movement
- **Gamma:** Rate of change of delta
- **Theta:** Time decay
- **Vega:** Volatility sensitivity
- **IV (Implied Volatility):** Market's expectation of volatility

---

## 📈 Database Statistics

### Current Data

- **Total Records:** 8,646,168
- **Unique Symbols:** 637
- **Data Period:** August 29, 2025 to January 2, 2026
- **Average Records per Day:** ~107,900 (for full trading days)
- **Database Size:** ~2.85 GB

### Data Integrity

- ✅ **No Duplicates:** Total records = Unique (symbol, ts) combinations
- ✅ **UPSERT Working:** All inserts use conflict resolution
- ✅ **Indexes Active:** All indexes are properly maintained

---

## 🔍 Common Queries

### Get Latest Data for a Symbol

```sql
SELECT * FROM ltp_ticks 
WHERE symbol = 'NIFTY06JAN2626250CE' 
ORDER BY ts DESC 
LIMIT 10;
```

### Get Data for a Date Range

```sql
SELECT * FROM ltp_ticks 
WHERE date(ts) = '2026-01-02'
ORDER BY ts, symbol;
```

### Get Unique Symbols

```sql
SELECT DISTINCT symbol 
FROM ltp_ticks 
ORDER BY symbol;
```

### Get Data Count by Date

```sql
SELECT date(ts) as date, COUNT(*) as records
FROM ltp_ticks
GROUP BY date(ts)
ORDER BY date DESC;
```

### Check for Duplicates (Should return 0)

```sql
SELECT COUNT(*) - COUNT(DISTINCT symbol || '_' || ts) as duplicates
FROM ltp_ticks;
```

---

## 🛠️ Maintenance

### Vacuum Database

```sql
VACUUM;
```

### Check Database Size

```bash
ls -lh /opt/nifty-data-collector/data/nifty_local.db
```

### Analyze Indexes

```sql
ANALYZE;
```

---

## 📝 Notes

1. **Primary Table:** `ltp_ticks` is the main active table
2. **OI Snapshots:** Currently not actively populated (0 records)
3. **Archive Table:** Contains historical OI data (7,021 records)
4. **Unique Constraints:** Two unique indexes exist on `(symbol, ts)` - redundant but harmless
5. **Data Source:** All data comes from WebSocket feeds (`source = 'ws'`)

---

**Last Verified:** January 3, 2026

