# Schema Comparison: SQLite Data Centre vs PostgreSQL Data Centre

**Reference (SQLite):** `G:\Projects\Centralize Data Centre`  
**This project (PostgreSQL):** `g:\Projects\Centralize Data Centre_PostgreSQL`  
**Date:** 2026-02-28

---

## Summary

| Aspect | SQLite (Reference) | PostgreSQL (This Project) | Match? |
|--------|--------------------|---------------------------|--------|
| **Tables** | 3 | 3 | Yes |
| **Table names** | ltp_ticks, oi_snapshots, oi_snapshots_archive | Same | Yes |
| **Columns (ltp_ticks)** | 15 columns | 15 columns (same logical fields) | Yes |
| **Columns (oi_snapshots)** | 11 columns | 11 columns | Yes |
| **Columns (oi_snapshots_archive)** | 10 columns | 10 columns | Yes |
| **Unique constraint** | (symbol, ts) via index | UNIQUE(symbol, ts) in table | Yes |
| **Indexes (ltp_ticks)** | ux_ltp_symbol_ts, idx_ticks_symbol_ts, idx_ticks_ts, idx_ticks_symbol | idx_ticks_symbol_ts, idx_ticks_ts, idx_ticks_symbol + UNIQUE(symbol, ts) | Yes |
| **Indexes (oi_snapshots)** | ux_oi_symbol_ts | UNIQUE(symbol, ts) in table | Yes |

**No tables are missing.** Structure and schemas are the same; only type names differ (e.g. SQLite `TEXT`/`REAL`/`INTEGER` → PostgreSQL `VARCHAR`/`DOUBLE PRECISION`/`BIGINT`/`TIMESTAMPTZ`).

---

## Tables

### 1. `ltp_ticks`

| SQLite (Reference) | PostgreSQL (This Project) |
|--------------------|----------------------------|
| id INTEGER PRIMARY KEY AUTOINCREMENT | id BIGSERIAL PRIMARY KEY |
| symbol TEXT NOT NULL | symbol VARCHAR(64) NOT NULL |
| token TEXT NOT NULL | token VARCHAR(32) NOT NULL |
| ts TEXT NOT NULL | ts TIMESTAMPTZ NOT NULL |
| ltp REAL | ltp DOUBLE PRECISION |
| bid REAL | bid DOUBLE PRECISION |
| ask REAL | ask DOUBLE PRECISION |
| volume INTEGER | volume BIGINT |
| oi INTEGER | oi BIGINT |
| delta REAL | delta DOUBLE PRECISION |
| gamma REAL | gamma DOUBLE PRECISION |
| theta REAL | theta DOUBLE PRECISION |
| vega REAL | vega DOUBLE PRECISION |
| iv REAL DEFAULT 0.0 | iv DOUBLE PRECISION DEFAULT 0.0 |
| source TEXT DEFAULT 'ws' | source VARCHAR(16) DEFAULT 'ws' |
| UNIQUE via index (symbol, ts) | UNIQUE(symbol, ts) |

Indexes: both have (symbol, ts), (ts), (symbol). PostgreSQL uses `CREATE INDEX`; SQLite uses unique index + regular indexes.

### 2. `oi_snapshots`

| SQLite (Reference) | PostgreSQL (This Project) |
|--------------------|----------------------------|
| id INTEGER PRIMARY KEY AUTOINCREMENT | id BIGSERIAL PRIMARY KEY |
| symbol TEXT NOT NULL | symbol VARCHAR(64) NOT NULL |
| token TEXT NOT NULL | token VARCHAR(32) NOT NULL |
| ts TEXT NOT NULL | ts TIMESTAMPTZ NOT NULL |
| oi INTEGER | oi BIGINT |
| volume INTEGER | volume BIGINT |
| delta REAL | delta DOUBLE PRECISION |
| gamma REAL | gamma DOUBLE PRECISION |
| theta REAL | theta DOUBLE PRECISION |
| vega REAL | vega DOUBLE PRECISION |
| iv REAL DEFAULT 0.0 | iv DOUBLE PRECISION DEFAULT 0.0 |
| UNIQUE via index (symbol, ts) | UNIQUE(symbol, ts) |

### 3. `oi_snapshots_archive`

| SQLite (Reference) | PostgreSQL (This Project) |
|--------------------|----------------------------|
| id INT | id BIGINT |
| symbol TEXT | symbol VARCHAR(64) |
| token TEXT | token VARCHAR(32) |
| ts TEXT | ts TIMESTAMPTZ |
| oi INT | oi BIGINT |
| volume INT | volume BIGINT |
| delta REAL | delta DOUBLE PRECISION |
| gamma REAL | gamma DOUBLE PRECISION |
| theta REAL | theta DOUBLE PRECISION |
| vega REAL | vega DOUBLE PRECISION |

No primary key or unique constraint in either project.

---

## What are `oi_snapshots` and `oi_snapshots_archive`?

### `oi_snapshots`
- **Purpose:** Intended for **Open Interest (OI) snapshot** data — one row per (symbol, timestamp) with OI, volume, and Greeks.
- **Current use:** The VPS schema doc says this table is **“currently not actively used”**. The live collector writes all data (LTP + OI + Greeks) into **`ltp_ticks`** only, so OI is already stored there. `oi_snapshots` exists for possible future use (e.g. a dedicated OI-only feed or reporting).

### `oi_snapshots_archive`
- **Purpose:** **Archive** for old OI snapshot rows (e.g. when pruning or moving data out of `oi_snapshots`).
- **Current use:** No code in this project writes to it. It’s a placeholder for historical OI storage if you ever start using `oi_snapshots` and need to archive old rows.

### Why are both empty in PostgreSQL?
1. **Sync only copies `ltp_ticks`** — `sync_nifty_db.py` and `full_db_sync.py` download and upsert only **ltp_ticks** from the VPS. They never sync `oi_snapshots` or `oi_snapshots_archive`.
2. **Collector writes only to `ltp_ticks`** — The VPS (and local) collector (`nifty_stream_local_sqlite.py`) does `insert_rows("ltp_ticks", batch)`; it does not insert into `oi_snapshots`.
3. **Same on the reference SQLite DB** — On the reference project, `oi_snapshots` is typically empty or lightly used, and `oi_snapshots_archive` is for future/optional use.

So empty `oi_snapshots` and `oi_snapshots_archive` in PostgreSQL are **expected** and match the current design. All live data you need is in **`ltp_ticks`** (including OI and Greeks).

---

## Other tables (reference project)

- **`service_metadata`** – Exists only in **archive** code (`archive/local_data_service.py`), not in the main SQLite data centre schema. The main sync and services use only the three tables above. Not present in PostgreSQL (and not required for parity).

---

## Conclusion

- **Same tables:** ltp_ticks, oi_snapshots, oi_snapshots_archive.  
- **Same columns** (with equivalent types).  
- **Same constraints:** UNIQUE(symbol, ts) for ltp_ticks and oi_snapshots.  
- **No missing tables** in the PostgreSQL data centre compared to the reference SQLite data centre.
