"""
Zero-Hero (expiry-day premium spike) detector.

Goal:
  Find option symbols expiring on a given date (expiry day), and detect rapid premium spikes like:
    10 -> 100, 5 -> 100, 20 -> 200, etc.

Data source:
  SQLite DB: project-root/data/nifty_local.db (table: ltp_ticks)

Examples:
  # One expiry date
  py Testing/zero_hero_detector.py --date 2025-09-30

  # Multiple dates
  py Testing/zero_hero_detector.py --date 2025-09-30 --date 2025-10-14

  # Custom spike definition (within 2 minutes, from <=15 to >=120)
  py Testing/zero_hero_detector.py --date 2025-09-30 --low-max 15 --high-min 120 --window-seconds 120
"""

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
from typing import Deque, Dict, Iterable, List, Optional, Tuple


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
    """
    Accepts local canonical ts like:
      2025-09-30T03:45:10
      2025-09-30T03:45:07.465691
    and VPS-style (occasionally present) like:
      2025-09-30T03:45:10+00:00
      2025-09-30T03:45:07.465691+00:00
    """
    t = ts.strip()
    if t.endswith("Z"):
        t = t[:-1]
    # If it's "+00:00", strip it to keep naive UTC comparable to local canonical.
    if t.endswith("+00:00"):
        t = t[:-6]
    dt = datetime.fromisoformat(t)
    return dt.timestamp()


def expiry_prefix_from_date(underlying: str, d: date) -> str:
    # Symbol format observed in DB: NIFTY02DEC2525700CE -> <UNDERLYING><DD><MMM><YY><STRIKE><CE/PE>
    dd = f"{d.day:02d}"
    mmm = MONTH_ABBR[d.month]
    yy = f"{d.year % 100:02d}"
    return f"{underlying.upper()}{dd}{mmm}{yy}"


@dataclass(frozen=True)
class SpikeEvent:
    expiry_date: str  # YYYY-MM-DD
    symbol: str
    option_type: str  # CE/PE/?
    start_ts: str
    end_ts: str
    duration_s: int
    ltp_start: float
    ltp_end: float
    delta_abs: float
    delta_mult: float
    volume_end: Optional[int]
    oi_end: Optional[int]
    iv_end: Optional[float]
    source_end: Optional[str]


def detect_spikes_for_symbol(
    rows: Iterable[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]],
    expiry_date_str: str,
    low_max: float,
    high_min: float,
    window_seconds: int,
    min_abs_increase: float,
    min_mult: float,
    cooldown_seconds: int,
) -> List[SpikeEvent]:
    """
    Streaming detector: rows must be in ascending time order for a single symbol.
    """
    events: List[SpikeEvent] = []
    low_candidates: Deque[Tuple[float, str, float]] = deque()  # (epoch_s, ts_str, ltp)
    last_event_end_epoch: Optional[float] = None

    last_volume: Optional[int] = None
    last_oi: Optional[int] = None
    last_iv: Optional[float] = None
    last_source: Optional[str] = None
    symbol: Optional[str] = None

    for ts, sym, ltp, volume, oi, iv, source in rows:
        symbol = sym
        if ltp is None:
            continue
        try:
            ltp_f = float(ltp)
        except Exception:
            continue
        if ltp_f <= 0:
            continue

        epoch = iso_ts_to_epoch_seconds(ts)

        # Cooldown after a spike to avoid spamming overlapping signals.
        if last_event_end_epoch is not None and epoch < (last_event_end_epoch + cooldown_seconds):
            last_volume, last_oi, last_iv, last_source = volume, oi, iv, source
            continue

        # Expire old candidates.
        while low_candidates and epoch - low_candidates[0][0] > window_seconds:
            low_candidates.popleft()

        # Add low candidate if under threshold.
        if ltp_f <= low_max:
            low_candidates.append((epoch, ts, ltp_f))

        # Check spike condition at current point.
        if ltp_f >= high_min and low_candidates:
            # Choose best low candidate within window (lowest ltp for largest jump)
            best = min(low_candidates, key=lambda x: x[2])
            start_epoch, start_ts, ltp_start = best
            duration = int(round(epoch - start_epoch))
            if duration < 0:
                # Shouldn't happen if ordered, but guard anyway.
                continue

            delta_abs = ltp_f - ltp_start
            delta_mult = (ltp_f / ltp_start) if ltp_start > 0 else float("inf")

            if delta_abs >= min_abs_increase and delta_mult >= min_mult:
                opt_type = sym[-2:] if len(sym) >= 2 else "?"
                events.append(
                    SpikeEvent(
                        expiry_date=expiry_date_str,
                        symbol=sym,
                        option_type=opt_type,
                        start_ts=start_ts,
                        end_ts=ts,
                        duration_s=duration,
                        ltp_start=round(ltp_start, 4),
                        ltp_end=round(ltp_f, 4),
                        delta_abs=round(delta_abs, 4),
                        delta_mult=round(delta_mult, 4),
                        volume_end=volume,
                        oi_end=oi,
                        iv_end=iv,
                        source_end=source,
                    )
                )
                last_event_end_epoch = epoch
                low_candidates.clear()  # reset for next move

        last_volume, last_oi, last_iv, last_source = volume, oi, iv, source

    return events


def iter_expiry_day_option_ticks(
    con,
    expiry_date_str: str,
    underlying: str,
) -> Iterable[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]]:
    """Yield rows for options expiring on expiry_date_str (PostgreSQL)."""
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


def write_events_csv(path: Path, events: List[SpikeEvent]) -> None:
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
                "volume_end",
                "oi_end",
                "iv_end",
                "source_end",
            ]
        )
        for e in events:
            w.writerow(
                [
                    e.expiry_date,
                    e.symbol,
                    e.option_type,
                    e.start_ts,
                    e.end_ts,
                    e.duration_s,
                    e.ltp_start,
                    e.ltp_end,
                    e.delta_abs,
                    e.delta_mult,
                    e.volume_end,
                    e.oi_end,
                    e.iv_end,
                    e.source_end,
                ]
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect expiry-day option premium spikes (Zero-Hero).")
    ap.add_argument(
        "--db",
        default=str(Path("data") / "nifty_local.db"),
        help="Path to local SQLite DB (default: data/nifty_local.db)",
    )
    ap.add_argument(
        "--underlying",
        default="NIFTY",
        help="Underlying prefix used in symbol (default: NIFTY).",
    )
    ap.add_argument(
        "--date",
        action="append",
        help="Expiry date to analyze (YYYY-MM-DD). Can be specified multiple times.",
    )
    ap.add_argument(
        "--start-date",
        help="Start date (YYYY-MM-DD) to scan day-by-day (inclusive).",
    )
    ap.add_argument(
        "--end-date",
        help="End date (YYYY-MM-DD) to scan day-by-day (inclusive).",
    )
    ap.add_argument("--low-max", type=float, default=20.0, help="Low premium upper bound (default: 20).")
    ap.add_argument("--high-min", type=float, default=100.0, help="High premium lower bound (default: 100).")
    ap.add_argument(
        "--window-seconds",
        type=int,
        default=300,
        help="Max duration for spike (seconds) between low and high (default: 300).",
    )
    ap.add_argument(
        "--min-abs-increase",
        type=float,
        default=50.0,
        help="Minimum absolute premium increase (default: 50).",
    )
    ap.add_argument(
        "--min-mult",
        type=float,
        default=3.0,
        help="Minimum multiplier increase (default: 3.0x).",
    )
    ap.add_argument(
        "--cooldown-seconds",
        type=int,
        default=120,
        help="Cooldown after a detected spike before searching again for same symbol (default: 120).",
    )
    ap.add_argument(
        "--out",
        default=str(Path("Testing") / "zero_hero_spike_events.csv"),
        help="Output CSV path (default: Testing/zero_hero_spike_events.csv)",
    )
    args = ap.parse_args()

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

    # Validate upfront
    for d in dates:
        try:
            parse_yyyy_mm_dd(d)
        except Exception:
            print(f"Invalid date: {d} (expected YYYY-MM-DD)")
            return 2

    try:
        con = db.get_connection()
    except Exception as e:
        print(f"DB connection failed: {e}. Set DATABASE_URL.")
        return 2

    all_events: List[SpikeEvent] = []

    for expiry_date_str in dates:
        rows = iter_expiry_day_option_ticks(con, expiry_date_str, args.underlying)

        # Stream by symbol.
        current_symbol: Optional[str] = None
        buffer_rows: List[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]] = []

        def flush_symbol(buf: List[Tuple[str, str, float, Optional[int], Optional[int], Optional[float], Optional[str]]]):
            if not buf:
                return
            sym = buf[0][1]
            events = detect_spikes_for_symbol(
                buf,
                expiry_date_str=expiry_date_str,
                low_max=args.low_max,
                high_min=args.high_min,
                window_seconds=args.window_seconds,
                min_abs_increase=args.min_abs_increase,
                min_mult=args.min_mult,
                cooldown_seconds=args.cooldown_seconds,
            )
            if events:
                print(f"{expiry_date_str} | {sym} | spikes={len(events)}")
            all_events.extend(events)

        for r in rows:
            ts, sym, *_ = r
            if current_symbol is None:
                current_symbol = sym
            if sym != current_symbol:
                flush_symbol(buffer_rows)
                buffer_rows = []
                current_symbol = sym
            buffer_rows.append(r)

        flush_symbol(buffer_rows)

    con.close()

    out_path = Path(args.out)
    write_events_csv(out_path, all_events)

    # Print a small summary.
    print(f"\nEvents found: {len(all_events)}")
    print(f"Wrote: {out_path}")

    # Top 10 by delta_abs
    top = sorted(all_events, key=lambda e: (e.delta_abs, e.delta_mult), reverse=True)[:10]
    if top:
        print("\nTop 10 spikes (by abs move):")
        for e in top:
            print(
                f"  {e.expiry_date} {e.symbol} {e.start_ts.split('T')[1]}->{e.end_ts.split('T')[1]} "
                f"{e.ltp_start}->{e.ltp_end} (Δ={e.delta_abs}, x{e.delta_mult}, {e.duration_s}s)"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


