#!/usr/bin/env python3
"""
Dry-run test for PostgreSQL setup.
- Loads .env and connects using DATABASE_URL
- Creates database from DATABASE_URL if it does not exist (connects to 'postgres' first)
- Runs init_postgres_schema (tables + indexes)
- Verifies ltp_ticks and oi_snapshots exist
"""

import sys
from pathlib import Path

# Ensure project root is on path and .env is loaded via services.db
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def main():
    import psycopg2
    from services import db

    url = db.get_database_url()
    # Parse dbname for "database does not exist" fallback (create it)
    dbname = "Centralized_Index_Option_Data"
    if "/" in url:
        parts = url.rstrip("/").split("/")
        if parts:
            dbname = parts[-1].split("?")[0] or dbname

    print("PostgreSQL dry run")
    print("  DATABASE_URL (masked):", url.split("@")[-1] if "@" in url else url[:50] + "...")
    print()

    conn = None
    try:
        conn = db.get_connection()
        print("  [OK] Connected to PostgreSQL")
    except psycopg2.OperationalError as e:
        if "does not exist" in str(e).lower() or "database" in str(e).lower():
            print("  Database", repr(dbname), "not found; attempting to create it...")
            # Connect to maintenance database and create target database
            try:
                # Override dbname in URL to connect to 'postgres'
                conn_admin = psycopg2.connect(url, dbname="postgres")
                conn_admin.autocommit = True
                from psycopg2 import sql
                with conn_admin.cursor() as cur:
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
                conn_admin.close()
                print("  [OK] Created database", dbname)
            except Exception as e2:
                err = str(e2).lower()
                if "permission denied" in err or "createdb" in err:
                    print("  [SKIP] User nifty_app cannot create databases (no CREATEDB privilege).")
                    print()
                    print("  Create the database once as a superuser (e.g. postgres), then re-run this script:")
                    print('    psql -U postgres -h localhost -p 5432 -c \'CREATE DATABASE "Centralized_Index_Option_Data" OWNER nifty_app;\'')
                    print()
                    print("  Or grant CREATEDB to nifty_app so this script can create it:")
                    print("    psql -U postgres -h localhost -p 5432 -c 'ALTER USER nifty_app CREATEDB;'")
                else:
                    print("  [FAIL] Could not create database:", e2)
                return 1
            conn = db.get_connection()
            print("  [OK] Connected to", dbname)
        else:
            print("  [FAIL] Connection error:", e)
            return 1
    except Exception as e:
        print("  [FAIL]", e)
        return 1

    try:
        db.init_postgres_schema(conn)
        print("  [OK] Schema initialized (ltp_ticks, oi_snapshots, oi_snapshots_archive, indexes)")
        assert db.table_exists(conn, "ltp_ticks"), "ltp_ticks missing"
        assert db.table_exists(conn, "oi_snapshots"), "oi_snapshots missing"
        assert db.table_exists(conn, "oi_snapshots_archive"), "oi_snapshots_archive missing"
        print("  [OK] Tables verified")
    finally:
        if conn:
            conn.close()

    print()
    print("Dry run completed successfully. PostgreSQL is ready for sync and services.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
