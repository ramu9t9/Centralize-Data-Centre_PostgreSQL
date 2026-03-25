#!/usr/bin/env python3
"""
PostgreSQL database abstraction for Centralize Data Centre.
Provides connection helpers and dialect-neutral utilities.

Timezone: All connections use session timezone Asia/Kolkata (IST). Timestamps
are stored as TIMESTAMPTZ (UTC instants). Inserts must pass UTC with '+00:00'
suffix (use ensure_utc_suffix) so the stored instant is correct when session is IST.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# Load .env from project root when present (so DATABASE_URL can be set there)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

import psycopg2
from psycopg2.extensions import connection as PgConnection


def get_database_url() -> str:
    """Return DATABASE_URL from environment. Raises if not set."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it in your environment or .env file, e.g.\n"
            "  DATABASE_URL=postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data"
        )
    return url


# Default timezone for all connections: IST (India). Timestamps display and date_trunc use IST.
# When inserting, pass UTC timestamps with explicit '+00:00' so the stored instant is correct.
POSTGRES_TIMEZONE = "Asia/Kolkata"


def get_connection(**kwargs) -> PgConnection:
    """Open and return a psycopg2 connection using DATABASE_URL. Session timezone is set to IST."""
    url = get_database_url()
    conn = psycopg2.connect(url, **kwargs)
    with conn.cursor() as cur:
        cur.execute("SET timezone = %s", (POSTGRES_TIMEZONE,))
    return conn


def ensure_utc_suffix(ts_str: Optional[str]) -> Optional[str]:
    """Ensure timestamp string has explicit UTC offset so PostgreSQL interprets it as UTC.
    Use when inserting into TIMESTAMPTZ so session timezone (IST) does not change the instant."""
    if not ts_str or not ts_str.strip():
        return ts_str
    s = ts_str.strip()
    if s.endswith("Z") or "+00:00" in s or (len(s) > 3 and s[-3] == "+"):
        return s
    if "T" in s and len(s) >= 19:
        return s[:19] + "+00:00"
    return s + "+00:00" if len(s) >= 19 else s


def table_exists(conn: PgConnection, table_name: str) -> bool:
    """Return True if the given table exists in the public schema."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
            """,
            (table_name,),
        )
        return cur.fetchone()[0]


@contextmanager
def get_cursor(commit: bool = False):
    """
    Context manager: yields (connection, cursor). Closes both on exit.
    If commit=True, commits on success and rollbacks on exception.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def row_to_dict(cur, row) -> dict:
    """Convert a psycopg2 row to a dict keyed by column names."""
    if row is None:
        return {}
    return {desc[0]: row[i] for i, desc in enumerate(cur.description)}


# -------- PostgreSQL DDL (for init_local_db) --------

DDL_LTP_TICKS = """
CREATE TABLE IF NOT EXISTS ltp_ticks (
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
"""

DDL_OI_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS oi_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(64) NOT NULL,
    token VARCHAR(32) NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    oi BIGINT,
    volume BIGINT,
    delta DOUBLE PRECISION,
    gamma DOUBLE PRECISION,
    theta DOUBLE PRECISION,
    vega DOUBLE PRECISION,
    iv DOUBLE PRECISION DEFAULT 0.0,
    UNIQUE(symbol, ts)
);
"""

DDL_OI_SNAPSHOTS_ARCHIVE = """
CREATE TABLE IF NOT EXISTS oi_snapshots_archive (
    id BIGINT,
    symbol VARCHAR(64),
    token VARCHAR(32),
    ts TIMESTAMPTZ,
    oi BIGINT,
    volume BIGINT,
    delta DOUBLE PRECISION,
    gamma DOUBLE PRECISION,
    theta DOUBLE PRECISION,
    vega DOUBLE PRECISION
);
"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ltp_ticks(symbol, ts);",
    "CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ltp_ticks(ts);",
    "CREATE INDEX IF NOT EXISTS idx_ticks_symbol ON ltp_ticks(symbol);",
]

# -------- 1-minute OHLC table (VPS-aligned) --------
# One row per (symbol, minute). ts is TIMESTAMPTZ (minute start in Asia/Kolkata wall time as stored instant).
# symbol as TEXT matches VPS. Populated by scripts/build_ohlc_1min.py.
DDL_OHLC_1MIN = """
CREATE TABLE IF NOT EXISTS ohlc_1min (
    symbol TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    oi BIGINT,
    delta DOUBLE PRECISION,
    iv DOUBLE PRECISION,
    gamma DOUBLE PRECISION,
    theta DOUBLE PRECISION,
    vega DOUBLE PRECISION,
    UNIQUE(symbol, ts)
);
"""
DDL_OHLC_1MIN_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ohlc_1min_symbol_ts ON ohlc_1min(symbol, ts);",
    "CREATE INDEX IF NOT EXISTS idx_ohlc_1min_ts ON ohlc_1min(ts);",
    "CREATE INDEX IF NOT EXISTS idx_ohlc_1min_symbol ON ohlc_1min(symbol);",
]

# View for backtesting: one row per (symbol, ts) with Date(IST), Time(IST), and all metrics.
# Matches the "Latest Records from LTP_TICKS" layout. Query by ts or date_ist for a snapshot.
DDL_VIEW_LTP_BACKTEST = """
CREATE OR REPLACE VIEW v_ltp_ticks_backtest AS
SELECT
    (ts AT TIME ZONE 'Asia/Kolkata')::date AS date_ist,
    to_char(ts AT TIME ZONE 'Asia/Kolkata', 'HH24:MI:SS') AS time_ist,
    symbol,
    ltp,
    volume,
    oi,
    delta,
    gamma,
    theta,
    vega,
    iv,
    ts,
    token,
    bid,
    ask,
    source
FROM ltp_ticks
"""


def _ohlc_1min_has_column(conn: PgConnection, col: str) -> bool:
    """Return True if ohlc_1min has the given column."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'ohlc_1min' AND column_name = %s
            """,
            (col,),
        )
        return cur.fetchone() is not None


def migrate_ohlc_1min_add_greeks(conn: PgConnection) -> None:
    """Add gamma, theta, vega to ohlc_1min if missing. Idempotent."""
    if not table_exists(conn, "ohlc_1min"):
        return
    with conn.cursor() as cur:
        for col in ("gamma", "theta", "vega"):
            if not _ohlc_1min_has_column(conn, col):
                cur.execute(f"ALTER TABLE ohlc_1min ADD COLUMN {col} DOUBLE PRECISION")
    conn.commit()


def init_postgres_schema(conn: PgConnection) -> None:
    """Create tables, indexes, and backtest view if they do not exist. Safe to call multiple times."""
    with conn.cursor() as cur:
        cur.execute(DDL_LTP_TICKS)
        cur.execute(DDL_OI_SNAPSHOTS)
        cur.execute(DDL_OI_SNAPSHOTS_ARCHIVE)
        for stmt in DDL_INDEXES:
            cur.execute(stmt)
        cur.execute(DDL_OHLC_1MIN)
        for stmt in DDL_OHLC_1MIN_INDEXES:
            cur.execute(stmt)
        cur.execute(DDL_VIEW_LTP_BACKTEST)
    conn.commit()
    # VPS parity: ohlc_1min.ts stays TIMESTAMPTZ (do not convert to timestamp without time zone).
    migrate_ohlc_1min_add_greeks(conn)
