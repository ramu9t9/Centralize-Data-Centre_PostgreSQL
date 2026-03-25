"""
Expiry-day option spike ranker (pattern discovery helper).

Instead of hard-coding "10 -> 100" thresholds, this script finds the *largest upward move*
within a rolling time window for each option symbol on its expiry date.

Output:
  CSV with one row per (expiry_date, symbol): best up-move found within window.

Examples:
  py Testing/expiry_day_spike_ranker.py --date 2025-09-30
  py Testing/expiry_day_spike_ranker.py --start-date 2025-09-20 --end-date 2025-10-05 --window-seconds 300
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import deque
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
from services import db
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Deque, Iterable, List, Optional, Tuple


MONTH_ABBR = {
    1: "JAN",
    2: "FEB",
    3: "MAR",
    4: "APR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AUG",
    9: "SEP",
    10: "OCT",
    11: "NOV",
    12: "DEC",
}


def parse_yyyy_mm_dd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def iso_ts_to_epoch_seconds(ts: str) -> float:
    t = ts.strip()
    if t.endswith("Z"):
        t = t[:-1]
    if t.endswith("+00:00"):
        t = t[:-6]
    return datetime.fromisoformat(t).timestamp()


def expiry_prefix_from_date(underlying: str, d: date) -> str:
    dd = f"{d.day:02d}"
    mmm = MONTH_ABBR[d.month]
    yy = f"{d.year % 100:02d}"
    return f"{underlying.upper()}{dd}{mmm}{yy}"


def iter_expiry_day_option_ticks(
    con,
    expiry_date_str: str,
    underlying: str,
) -> Iterable[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]]:
    d = parse_yyyy_mm_dd(expiry_date_str)
    prefix = expiry_prefix_from_date(underlying, d)
    cur = con.cursor()
    cur.execute(
        """
        SELECT ts, symbol, ltp, volume, oi, iv, source
        FROM ltp_ticks
        WHERE ts::date = %s::date
          AND (symbol LIKE %s OR symbol LIKE %s)
          AND ltp IS NOT NULL
        ORDER BY symbol ASC, ts ASC
        """,
        (expiry_date_str, prefix + "%CE", prefix + "%PE"),
    )
    for row in cur:
        ts_val = row[0]
        ts_str = str(ts_val).replace(" ", "T")[:19] if ts_val else ""
        yield (ts_str, row[1], row[2], row[3], row[4], row[5], row[6])


@dataclass(frozen=True)
class BestMove:
    expiry_date: str
    symbol: str
    option_type: str
    start_ts: str
    end_ts: str
    duration_s: int
    ltp_start: float
    ltp_end: float
    delta_abs: float
    delta_mult: float


def best_up_move_for_symbol(
    rows: Iterable[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]],
    expiry_date_str: str,
    window_seconds: int,
    min_start_ltp: float,
    max_start_ltp: float,
) -> Optional[BestMove]:
    """
    For each point, compare current ltp to the minimum ltp within the last window_seconds.
    """
    low_points: Deque[Tuple[float, str, float]] = deque()  # (epoch, ts, ltp)
    best: Optional[BestMove] = None

    for ts, sym, ltp, *_ in rows:
        if ltp is None:
            continue
        try:
            ltp_f = float(ltp)
        except Exception:
            continue
        if ltp_f <= 0:
            continue

        epoch = iso_ts_to_epoch_seconds(ts)
        while low_points and epoch - low_points[0][0] > window_seconds:
            low_points.popleft()

        # Track candidate starts only within [min_start_ltp, max_start_ltp]
        if min_start_ltp <= ltp_f <= max_start_ltp:
            # Maintain deque increasing by ltp (monotonic min queue)
            while low_points and low_points[-1][2] >= ltp_f:
                low_points.pop()
            low_points.append((epoch, ts, ltp_f))

        if not low_points:
            continue

        start_epoch, start_ts, start_ltp = low_points[0]
        if start_ltp <= 0:
            continue

        delta_abs = ltp_f - start_ltp
        if delta_abs <= 0:
            continue
        delta_mult = ltp_f / start_ltp
        duration = int(round(epoch - start_epoch))
        opt_type = sym[-2:] if len(sym) >= 2 else "?"

        candidate = BestMove(
            expiry_date=expiry_date_str,
            symbol=sym,
            option_type=opt_type,
            start_ts=start_ts,
            end_ts=ts,
            duration_s=max(0, duration),
            ltp_start=round(start_ltp, 4),
            ltp_end=round(ltp_f, 4),
            delta_abs=round(delta_abs, 4),
            delta_mult=round(delta_mult, 4),
        )

        if best is None or (candidate.delta_abs, candidate.delta_mult) > (best.delta_abs, best.delta_mult):
            best = candidate

    return best


def write_csv(path: Path, rows: List[BestMove]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "expiry_date",
                "symbol",
                "option_type",
                "start_ts",
                "end_ts",
                "duration_s",
                "ltp_start",
                "ltp_end",
                "delta_abs",
                "delta_mult",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.expiry_date,
                    r.symbol,
                    r.option_type,
                    r.start_ts,
                    r.end_ts,
                    r.duration_s,
                    r.ltp_start,
                    r.ltp_end,
                    r.delta_abs,
                    r.delta_mult,
                ]
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Rank expiry-day option symbols by best upward move within a window.")
    ap.add_argument("--db", default=str(Path("data") / "nifty_local.db"))
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--date", action="append", help="Expiry date to analyze (YYYY-MM-DD). Can be repeated.")
    ap.add_argument("--start-date", help="Start date (YYYY-MM-DD) to scan day-by-day (inclusive).")
    ap.add_argument("--end-date", help="End date (YYYY-MM-DD) to scan day-by-day (inclusive).")
    ap.add_argument("--window-seconds", type=int, default=300)
    ap.add_argument(
        "--min-start-ltp",
        type=float,
        default=0.05,
        help="Only consider starts with ltp >= this (default: 0.05).",
    )
    ap.add_argument(
        "--max-start-ltp",
        type=float,
        default=50.0,
        help="Only consider starts with ltp <= this (default: 50).",
    )
    ap.add_argument("--out", default=str(Path("Testing") / "expiry_day_best_up_moves.csv"))
    args = ap.parse_args()

    db_path = Path(args.db)

    dates: List[str] = []
    if args.date:
        dates.extend([d.strip() for d in args.date if d and d.strip()])

    if args.start_date or args.end_date:
        if not (args.start_date and args.end_date):
            print("If using --start-date/--end-date, both must be provided.")
            return 2
        try:
            sd = parse_yyyy_mm_dd(args.start_date)
            ed = parse_yyyy_mm_dd(args.end_date)
        except Exception:
            print("Invalid --start-date/--end-date (expected YYYY-MM-DD).")
            return 2
        if ed < sd:
            print("--end-date must be >= --start-date")
            return 2
        cur = sd
        while cur <= ed:
            dates.append(cur.strftime("%Y-%m-%d"))
            cur = date.fromordinal(cur.toordinal() + 1)

    if not dates:
        print("Provide either --date (one or more) OR --start-date and --end-date.")
        return 2

    try:
        con = db.get_connection()
    except Exception as e:
        print(f"DB connection failed: {e}. Set DATABASE_URL.")
        return 2
    all_rows: List[BestMove] = []

    for expiry_date_str in dates:
        rows = iter_expiry_day_option_ticks(con, expiry_date_str, args.underlying)

        current_symbol: Optional[str] = None
        buf: List[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]] = []

        def flush():
            if not buf:
                return
            best = best_up_move_for_symbol(
                buf,
                expiry_date_str=expiry_date_str,
                window_seconds=args.window_seconds,
                min_start_ltp=args.min_start_ltp,
                max_start_ltp=args.max_start_ltp,
            )
            if best is not None:
                all_rows.append(best)

        for r in rows:
            ts, sym, *_ = r
            if current_symbol is None:
                current_symbol = sym
            if sym != current_symbol:
                flush()
                buf = []
                current_symbol = sym
            buf.append(r)
        flush()

    con.close()

    out_path = Path(args.out)
    write_csv(out_path, all_rows)

    print(f"Rows written: {len(all_rows)}")
    print(f"Wrote: {out_path}")

    top = sorted(all_rows, key=lambda x: (x.delta_mult, x.delta_abs), reverse=True)[:15]
    if top:
        print("\nTop 15 by multiplier:")
        for r in top:
            print(
                f"  {r.expiry_date} {r.symbol} {r.start_ts.split('T')[1]}->{r.end_ts.split('T')[1]} "
                f"{r.ltp_start}->{r.ltp_end} (x{r.delta_mult}, Δ={r.delta_abs}, {r.duration_s}s)"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


