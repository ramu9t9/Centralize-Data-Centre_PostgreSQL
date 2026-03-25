# Cross-verification: PostgreSQL vs Reference (Centralize Data Centre)

**Reference project:** `G:\Projects\Centralize Data Centre` (SQLite)  
**This project:** `g:\Projects\Centralize Data Centre_PostgreSQL` (PostgreSQL)  
**Purpose:** Confirm feature and behavior parity; document differences.

---

## 1. Project structure

| Area | Reference (SQLite) | PostgreSQL | Match / note |
|------|--------------------|------------|--------------|
| **Root** | README, requirements, .env, start/check .bat, sync_nifty_db.ps1 (wrapper) | Same + Important_commands.md at root, .env.example | ✅ Same intent |
| **services/** | sync_nifty_db, full_db_sync, data_sync_service, websocket_broadcaster, broadcast_control_panel, realtime_client, utils, start_broadcast | Same modules | ✅ Same |
| **scripts/** | get_sample_data, monitor_live_mode, verify_*, test_*, start_*.ps1, check_vps_process.ps1 | Same + dry_run_postgres, create_db.sql | ✅ Same |
| **Testing/** | Backtest, Greeks, patterns, expiry, zero_hero, CSV outputs | Same set | ✅ Same |
| **docs/** | DATABASE_SCHEMA_REFERENCE, Important_command, SYNC, VPS, walkthrough, etc. | Same + SCHEMA_COMPARISON_SQLite_vs_PostgreSQL, this CROSS_VERIFICATION | ✅ Superset |
| **data/** | nifty_local.db, sync_log, backups | Logs, backups (DB on server) | ✅ Same role, no local DB file |
| **vps_system/** | nifty_stream_local_sqlite, README, requirements | Same | ✅ Same |

---

## 2. Database and schema

| Item | Reference | PostgreSQL | Match / note |
|------|-----------|------------|--------------|
| **DB engine** | SQLite 3, `data/nifty_local.db` | PostgreSQL, `DATABASE_URL` (e.g. Centralized_Index_Option_Data) | Different by design |
| **DB access** | Inline `sqlite3` in sync, broadcast, scripts | Central `services/db.py`: `get_connection()`, `init_postgres_schema()`, `ensure_utc_suffix()` | ✅ Equivalent |
| **ltp_ticks** | 15 columns, ts TEXT (UTC), UNIQUE(symbol,ts) | Same columns; ts TIMESTAMPTZ (UTC); UNIQUE(symbol,ts) | ✅ Same (see SCHEMA_COMPARISON) |
| **oi_snapshots** | 11 columns | Same | ✅ Same |
| **oi_snapshots_archive** | 10 columns | Same | ✅ Same |
| **ohlc_1min** | Not present; OHLC from ticks in Testing (pandas resample). So reference has no “outside market hours” count. | Present: ts IST; only 09:15–15:30 weekdays inserted (no post‑market/weekend rows) | ➕ PG addition |
| **View** | None | v_ltp_ticks_backtest (date_ist, time_ist, …) | ➕ PG addition |

Schema details: see `docs/SCHEMA_COMPARISON_SQLite_vs_PostgreSQL.md`.

---

## 3. Timezone and market hours

| Item | Reference | PostgreSQL | Match / note |
|------|-----------|------------|--------------|
| **Market hours** | 09:15–15:30 IST | 09:15–15:30 IST | ✅ Same |
| **09:15 IST =** | 03:45 UTC (in sync, data_sync, broadcast) | 03:45 UTC (sync, data_sync, broadcast, db comments) | ✅ Same |
| **ltp_ticks storage** | UTC (canonical text, no TZ suffix) | UTC (TIMESTAMPTZ); inserts use `ensure_utc_suffix()` when session is IST | ✅ Same intent |
| **Display** | `services/utils.py`: `utc_to_ist()`, `format_timestamp_for_display()` | Same `utils.py`; session timezone Asia/Kolkata for display | ✅ Same |
| **Replay / GUI** | IST for UI; convert to UTC for DB queries | Same (broadcast_control_panel: IST → UTC for query) | ✅ Same |
| **ohlc_1min** | N/A | Table exists (timestamp without time zone); no build scripts | ➕ PG only |

---

## 4. Sync and services

| Component | Reference | PostgreSQL | Match / note |
|-----------|-----------|------------|--------------|
| **sync_nifty_db.py** | VPS SQLite → local SQLite; gap fill; 09:15 IST start; normalize ts | Same logic; writes to PostgreSQL via `db.get_connection()`, parameterized queries, `ensure_utc_suffix` for ts | ✅ Parity |
| **full_db_sync.py** | Full refresh, create tables if not exist | Same; uses `db.init_postgres_schema()` for tables | ✅ Parity |
| **data_sync_service.py** | Market open 9:15 IST = 3:45 UTC; last 24h fallback | Same | ✅ Parity |
| **broadcast_control_panel.py** | Replay/live, date range, IST display, UTC for DB | Same; DB via `db.get_connection()` | ✅ Parity |
| **websocket_broadcaster_service** | Broadcasts from DB to clients | Same (PostgreSQL queries) | ✅ Parity |
| **realtime_client** | Queries ltp_ticks | Same (PostgreSQL) | ✅ Parity |

---

## 5. Commands (Important_commands vs reference Important_command)

| Purpose | Reference (docs/Important_command.md) | PostgreSQL (Important_commands.md) | Match / note |
|---------|--------------------------------------|------------------------------------|--------------|
| **Sync (one-time)** | `.\sync_nifty_db.ps1` or `py sync_nifty_db.py` | `py -3 services/sync_nifty_db.py` | ✅ Same; path differs (PG uses services/) |
| **Sync force/auto** | `-Force` / `-Auto`; `--force` / `--auto` | `--force` / `--auto` | ✅ Same |
| **Backfill volume** | `--backfill-volume` (and date range) | Not in Important_commands (script supports it) | ⚠️ Add to PG doc if needed |
| **DB check / init** | Manual SQLite; verify_final_status, check_volume_status, etc. | `py -3 scripts/dry_run_postgres.py` (create DB + init schema) | ✅ Equivalent |
| **Full refresh** | full_db_sync / sync when empty | `py -3 services/full_db_sync.py` | ✅ Same |
| **Verify** | verify_final_status, check_volume_status, check_iv_data, verify_aug20, etc. | verify_data_broadcasting, verify_vps_data_collection, monitor_live_mode | ⚠️ Some ref scripts in PG archive |
| **GUI** | broadcast_control_panel | Same | ✅ Same |
| **DB create** | N/A | `scripts/create_db.sql`, `dry_run_postgres.py` | ➕ PG only |
| **SSH/VPS** | Same host, key, paths | Same (VPS unchanged) | ✅ Same |

---

## 6. Scripts only in reference (or in PG archive)

These exist in the reference project; in PostgreSQL many are under `archive/` or not present:

- **check_volume_status.py**, **check_iv_data.py**, **verify_final_status.py** – in PG: `archive/` or similar verify scripts.
- **verify_aug20_start.py**, **check_aug19_*.py**, **delete_aug14_18_records.py**, **delete_aug19_complete.py**, **delete_before_aug29.py** – one-off/cleanup; in PG: under `archive/` if kept.

If you need exact parity for verification workflows, copy or adapt these from the reference project and point them at PostgreSQL (using `services/db.py`).

---

## 7. Summary

| Category | Status |
|----------|--------|
| **Structure** | ✅ Same layout; PostgreSQL adds OHLC scripts and PG-specific helpers. |
| **Schema** | ✅ ltp_ticks, oi_snapshots, oi_snapshots_archive match (see SCHEMA_COMPARISON). PostgreSQL adds ohlc_1min and v_ltp_ticks_backtest. |
| **Timezone** | ✅ 09:15–15:30 IST, 03:45 UTC = 09:15 IST, ltp_ticks UTC, display IST. ohlc_1min in PG is IST for backtesting. |
| **Sync & services** | ✅ Same behavior; PostgreSQL uses db.py and parameterized queries. |
| **Commands** | ✅ Core sync/full/verify/GUI aligned; PG doc can add backfill-volume and any ref verify scripts you still use. |

**Conclusion:** The PostgreSQL project is aligned with the reference project for sync, schema (core tables), timezone, and services. Differences are: (1) database engine and connection layer, (2) added ohlc_1min and backtest view in PostgreSQL, (3) some reference verification/cleanup scripts in PG archive or omitted. Cross-verification with the reference project is satisfied for the shared scope above.
