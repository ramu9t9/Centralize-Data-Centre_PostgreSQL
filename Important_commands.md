# Important Commands — PostgreSQL Data Centre

Run these from the **project root** (e.g. `G:\Projects\Centralize Data Centre_PostgreSQL`).  
Ensure `.env` exists with `DATABASE_URL` set.

### HostITSmart — SSH and PostgreSQL

**SSH into the VPS**

```bash
ssh -i ~/.ssh/nifty_server_key -p 7576 root@103.168.18.35
```

**Connect to Postgres on the VPS** (DB listens on localhost there):

```bash
export PGPASSWORD='nifty_shadow_db_2026'
psql -U nifty_app -h 127.0.0.1 -d nifty_data
```

One line:

```bash
PGPASSWORD=nifty_shadow_db_2026 psql -U nifty_app -h 127.0.0.1 -d nifty_data
```

**Typical values**

| Item | Value |
|------|--------|
| Host (on VPS) | `127.0.0.1` |
| User | `nifty_app` |
| Database | `nifty_data` |
| Password | `nifty_shadow_db_2026` |

If your server differs, on the VPS run:

```bash
grep -E 'POSTGRES|DATABASE|PGPASSWORD|nifty_app' /opt/nifty-data-collector/.env 2>/dev/null
```

**From Windows (PowerShell)** — optional manual tunnel for DBeaver / pgAdmin (then connect to local port):

```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key -p 7576 -L 5433:127.0.0.1:5432 -N root@103.168.18.35
```

Use host `127.0.0.1`, port `5433`, user `nifty_app`, database `nifty_data`, password as above.

**Sync from this project:** In `.env` set `REMOTE_DATABASE_URL=postgresql://nifty_app:PASSWORD@103.168.18.35:5432/nifty_data` and `REMOTE_PG_SSH_TUNNEL=1`. Sync opens its own SSH tunnel (default local port `5433` via `REMOTE_PG_TUNNEL_LOCAL_PORT`) and pulls `ltp_ticks` into local `DATABASE_URL`. The SQLite file on HostITSmart is often empty; use Postgres.

Cross-verification with the reference project (SQLite): see [docs/CROSS_VERIFICATION_REFERENCE_PROJECT.md](docs/CROSS_VERIFICATION_REFERENCE_PROJECT.md).

**VPS schema parity:** Local `ltp_ticks` (partitioned), `ohlc_1min` (`ts` = `timestamptz`, `symbol` = `text`), and related tables should match VPS. `services/db.py` no longer converts `ohlc_1min.ts` to `timestamp without time zone`. `build_ohlc_1min.py` writes IST minute starts as `timestamptz`.

**Sync source:** Set `REMOTE_DATABASE_URL` (or `SYNC_SOURCE_DATABASE_URL`) in `.env` to pull `ltp_ticks` from **remote PostgreSQL** (same schema). Leave it unset to use **SSH + SQLite** on the VPS configured below. `DATABASE_URL` is always your **local** destination. `--trim-to-vps` / auto-trim when local has extra rows are **not** supported for the PostgreSQL source (use truncate + full refresh or fix counts manually).

**SSH to collectors (manual test):** Sync scripts read `VPS_HOST`, `VPS_SSH_PORT` (default `22`), `VPS_USER`, `SSH_KEY_PATH` from `.env` via `services/ssh_vps.py`.

| Provider | Host | SSH port | PowerShell |
|----------|------|----------|------------|
| **Hostinger** | `31.97.233.93` | `22` (default) | `ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93` |
| **HostITSmart** | `103.168.18.35` | `7576` | `ssh -i $env:USERPROFILE\.ssh\nifty_server_key -p 7576 root@103.168.18.35` |

For **HostITSmart** SSH (diagnostics / SQLite-only collectors): `VPS_HOST=103.168.18.35`, `VPS_SSH_PORT=7576`. If `nifty_local.db` is empty there, use **`REMOTE_DATABASE_URL`** (see above), not SQLite.

```powershell
cd "G:\Projects\Centralize Data Centre_PostgreSQL"
```

---

----------------------------------------------------------
✅ RECOMMENDED WORKFLOW (Fast Sync)
----------------------------------------------------------
# 1. Smart sync — only fetches mismatched days (5-15 min for 3-5 day gap)
py -3 services/sync_smart.py

# 2. Build 1-min OHLC from ticks (required after sync)
py -3 scripts/build_ohlc_1min.py
----------------------------------------------------------



## When you get time (e.g. every 3–5 days)

### 1. ⚡ Smart Sync — Fast Day-Fingerprint Sync (RECOMMENDED)
```powershell
$env:PYTHONIOENCODING='utf-8'
py -3 services/sync_smart.py
```
Compares per-day row counts (local vs VPS) and only fetches mismatched days.
Typical time: **5–15 minutes** for a 3-5 day gap. VPS PostgreSQL must be reachable via SSH tunnel (configured in `.env`).

```powershell
py -3 services/sync_smart.py --dry-run              # Preview diff table (no data written)
py -3 services/sync_smart.py --days 10              # Check only last 10 calendar days
py -3 services/sync_smart.py --date 2026-03-20      # Sync single IST date
py -3 services/sync_smart.py --force-date 2026-03-20 # Force re-fetch even if counts match
py -3 services/sync_smart.py --from 2026-03-01 --to 2026-03-25  # Sync a date range
```

### 2. (Fallback) Full sync — old row-streaming sync
```powershell
py -3 services/sync_nifty_db.py
```
Use when `sync_smart.py` fails or for initial full download. Can take 4+ hours for large datasets.

### 3. Build 1-minute OHLC from ticks (after sync)
```powershell
py -3 scripts/build_ohlc_1min.py
```
Converts 5-sec `ltp_ticks` → 1-min OHLC in `ohlc_1min` (IST). Continues from last built minute; run after each VPS sync.
```powershell
py -3 scripts/build_ohlc_1min.py --rebuild       # Rebuild entire ohlc_1min from scratch
py -3 scripts/build_ohlc_1min.py --fast         # Faster: skip tick count per date
py -3 scripts/build_ohlc_1min.py --limit 2      # Process only first N dates (for testing)
py -3 scripts/build_ohlc_1min.py --dry-run      # Show what would be done
```

---

## One-time or rare

### Check PostgreSQL and create tables
```powershell
py -3 scripts/dry_run_postgres.py
```
Creates DB if missing, inits schema (tables + indexes). Safe to run anytime.

### Full sync from VPS (replace all local data)
```powershell
py -3 services/full_db_sync.py
```
Or run `sync_nifty_db.py` when local DB is empty (it will trigger full download).  
Truncates local `ltp_ticks` and re-downloads everything from VPS.

---

## Optional

### Broadcast control panel (GUI)
```powershell
py -3 services/broadcast_control_panel.py
```

### Verify data / broadcasting
```powershell
py -3 scripts/verify_data_broadcasting.py
py -3 scripts/verify_vps_data_collection.py
py -3 scripts/monitor_live_mode.py
```

### Sync with extra options
```powershell
py -3 services/sync_nifty_db.py --force    # Sync even if local is newer
py -3 services/sync_nifty_db.py --auto     # Run every hour
py -3 services/sync_nifty_db.py --backfill-volume   # Backfill volume data
py -3 services/sync_nifty_db.py --backfill-volume --backfill-date "2025-08-20:2025-08-25"
```

---

## Create database (only if not exists)

If `dry_run_postgres.py` fails with “permission denied to create database”, create the DB as superuser:

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -f "G:\Projects\Centralize Data Centre_PostgreSQL\scripts\create_db.sql"
```
Or single line:
```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -c 'CREATE DATABASE "Centralized_Index_Option_Data" OWNER nifty_app;'
```

Use **`-h 127.0.0.1`** (not `localhost`) if you hit password prompts with default `pg_hba.conf`.