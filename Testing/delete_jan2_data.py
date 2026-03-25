import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

conn = db.get_connection()
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM ltp_ticks WHERE ts >= %s::timestamptz", ('2026-01-02T00:00:00',))
before_count = cursor.fetchone()[0]
print(f"Records to delete (from Jan 2, 2026 onwards): {before_count:,}")

cursor.execute("DELETE FROM ltp_ticks WHERE ts >= %s::timestamptz", ('2026-01-02T00:00:00',))
conn.commit()

cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
after_count = cursor.fetchone()[0]
print(f"Deleted: {before_count:,} records")
print(f"Remaining: {after_count:,} records")
conn.close()
