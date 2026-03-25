"""
ATM Options Premium Run Length Analysis
Analyzes continuation signals in ATM CE/PE premium movement
Includes body % and range % statistics per run length
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

def get_atm_strike(spot_price):
    """Round to nearest 50 for NIFTY ATM"""
    return round(spot_price / 50) * 50

def get_available_dates(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT (ts::date)::text FROM ltp_ticks WHERE symbol = 'NIFTY 50' ORDER BY ts::date")
    return [row[0] for row in cursor.fetchall()]

def get_expiry_for_date(conn, date):
    """Get nearest weekly expiry for given date"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT substring(symbol from 6 for 7) as expiry
        FROM ltp_ticks
        WHERE symbol LIKE 'NIFTY%%'
          AND symbol != 'NIFTY 50'
          AND symbol NOT LIKE '%%FUT'
          AND (symbol LIKE '%%CE' OR symbol LIKE '%%PE')
          AND ts::date = %s::date
        ORDER BY expiry
        LIMIT 1
    """, (date,))
    result = cursor.fetchone()
    return result[0] if result else None

def build_atm_premium_data(conn, date, expiry, option_type='CE'):
    """
    Build continuous ATM premium series for the day
    option_type: 'CE' or 'PE'
    """
    # Get spot data
    spot_query = f"SELECT ts, ltp FROM ltp_ticks WHERE symbol = 'NIFTY 50' AND ts::date = '{date}'"
    spot_df = pd.read_sql_query(spot_query, conn)
    spot_df['ts'] = pd.to_datetime(spot_df['ts'], format='mixed')
    spot_df = spot_df.sort_values('ts').set_index('ts')
    
    # Resample to 5s for smoother ATM tracking
    spot_5s = spot_df['ltp'].resample('5S').last().ffill()
    
    premium_data = []
    
    for ts, spot_price in spot_5s.items():
        atm_strike = get_atm_strike(spot_price)
        symbol = f"NIFTY{expiry}{atm_strike}{option_type}"
        
        # Get option premium at this timestamp
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ltp FROM ltp_ticks
            WHERE symbol = ?
              AND datetime(REPLACE(ts, 'T', ' ')) <= datetime(?)
            ORDER BY datetime(REPLACE(ts, 'T', ' ')) DESC
            LIMIT 1
        """, (symbol, ts.strftime('%Y-%m-%d %H:%M:%S')))
        
        result = cursor.fetchone()
        if result and result[0]:
            premium_data.append({
                'ts': ts,
                'premium': result[0],
                'strike': atm_strike
            })
    
    if not premium_data:
        return pd.DataFrame()
        
    premium_df = pd.DataFrame(premium_data).set_index('ts')
    return premium_df

def build_candles(df, timeframe_seconds):
    """Build OHLC candles from premium data"""
    if df.empty:
        return pd.DataFrame()
        
    candles = df['premium'].resample(f'{timeframe_seconds}S').ohlc()
    candles = candles.dropna()
    
    # Calculate metrics
    candles['body_size'] = abs(candles['close'] - candles['open'])
    candles['body_pct'] = (candles['body_size'] / candles['open']) * 100
    candles['range'] = candles['high'] - candles['low']
    candles['range_pct'] = (candles['range'] / candles['open']) * 100
    
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
                           
    return candles

def analyze_runs_with_metrics(candles):
    """Calculate run lengths with body % and range % statistics - UPWARD ONLY"""
    candles['prev_direction'] = candles['direction'].shift(1)
    
    # ONLY UPWARD MOVEMENTS (direction = 1) since we're BUYING premium
    signal_mask = (candles['direction'] == 1) & (candles['prev_direction'] == 1)
    
    directions = candles['direction'].values
    body_pcts = candles['body_pct'].values
    range_pcts = candles['range_pct'].values
    
    indices = np.where(signal_mask)[0]
    
    run_data = []
    
    for idx in indices:
        run = 0
        
        # Collect metrics for this run
        run_body_pcts = []
        run_range_pcts = []
        
        # Look ahead - continue while UPWARD (direction = 1)
        next_idx = idx + 1
        while next_idx < len(directions):
            if directions[next_idx] == 1:  # Only count upward candles
                run += 1
                run_body_pcts.append(body_pcts[next_idx])
                run_range_pcts.append(range_pcts[next_idx])
                next_idx += 1
            else:
                break
        
        run_data.append({
            'run_length': run,
            'avg_body_pct': np.mean(run_body_pcts) if run_body_pcts else 0,
            'avg_range_pct': np.mean(run_range_pcts) if run_range_pcts else 0
        })
    
    return run_data

def print_stats(run_data, label):
    if not run_data:
        print(f"\nNo data for {label}")
        return

    df = pd.DataFrame(run_data)
    total_signals = len(df)
    max_run = df['run_length'].max()
    
    print(f"\n{'='*80}")
    print(f"{label}")
    print(f"{'='*80}")
    print(f"Total Continuation Signals: {total_signals:,}")
    print(f"Maximum Consecutive Candles: {max_run}")
    
    print(f"\n{'Run':<6} | {'Count':<8} | {'% Occur':<8} | {'Prob%':<8} | {'Avg Body%':<12} | {'Avg Range%':<12}")
    print("-" * 80)
    
    for length in range(min(15, max_run + 1)):
        subset = df[df['run_length'] == length]
        count = len(subset)
        pct = (count / total_signals) * 100
        
        # Cumulative probability
        at_least_n = len(df[df['run_length'] >= length])
        cum_prob = (at_least_n / total_signals) * 100
        
        avg_body = subset['avg_body_pct'].mean() if len(subset) > 0 else 0
        avg_range = subset['avg_range_pct'].mean() if len(subset) > 0 else 0
        
        print(f"{length:<6} | {count:<8,} | {pct:<8.1f} | {cum_prob:<8.1f} | {avg_body:<12.3f} | {avg_range:<12.3f}")

def run_analysis(option_type='CE'):
    start_time = time.time()
    conn = db.get_connection()
    dates = get_available_dates(conn)
    
    print(f"\n{'='*80}")
    print(f"ATM {option_type} PREMIUM ANALYSIS - UPWARD TRENDS ONLY")
    print(f"{'='*80}")
    print(f"Total dates to process: {len(dates)}")
    print(f"Analyzing upward premium movements (buying scenario)")
    print(f"{'='*80}\n")
    
    results_20s = []
    results_30s = []
    
    processed = 0
    skipped = 0
    
    for date in dates:
        processed += 1
        print(f"[{processed}/{len(dates)}] Processing {date}...", end=' ')
        
        expiry = get_expiry_for_date(conn, date)
        if not expiry:
            print("❌ No expiry found")
            skipped += 1
            continue
            
        try:
            print(f"Expiry: {expiry}, Building ATM {option_type} series...", end=' ')
            premium_df = build_atm_premium_data(conn, date, expiry, option_type)
            
            if premium_df.empty or len(premium_df) < 100:
                print("❌ Insufficient data")
                skipped += 1
                continue
            
            print(f"✓ {len(premium_df)} ticks", end=' ')
                
            # 20s Analysis
            c20 = build_candles(premium_df, 20)
            if len(c20) > 0:
                runs20 = analyze_runs_with_metrics(c20)
                results_20s.extend(runs20)
                print(f"| 20s: {len(runs20)} signals", end=' ')
            
            # 30s Analysis
            c30 = build_candles(premium_df, 30)
            if len(c30) > 0:
                runs30 = analyze_runs_with_metrics(c30)
                results_30s.extend(runs30)
                print(f"| 30s: {len(runs30)} signals", end=' ')
            
            print("✓")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            skipped += 1
            
    conn.close()
    
    print(f"\n{'='*80}")
    print(f"Processing complete!")
    print(f"Processed: {processed} days | Skipped: {skipped} days")
    print(f"Time taken: {time.time() - start_time:.2f} seconds")
    print(f"{'='*80}\n")
    
    print_stats(results_20s, f"20-SECOND ATM {option_type} PREMIUM (UPWARD ONLY)")
    print_stats(results_30s, f"30-SECOND ATM {option_type} PREMIUM (UPWARD ONLY)")

if __name__ == "__main__":
    print("Choose option type:")
    print("1. ATM CE (Call)")
    print("2. ATM PE (Put)")
    choice = input("Enter choice (1/2): ").strip()
    
    option_type = 'CE' if choice == '1' else 'PE'
    run_analysis(option_type)
