"""
Multi-Month Run Length Analysis
Analyzes 4 months of NIFTY 50 data to determine:
1. Maximum number of consecutive candles in same direction
2. Probability of run extension (e.g., if we have 2, what's chance of 3?)
3. Distribution of run lengths for 20s and 30s timeframes
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db
import pandas as pd
import numpy as np
from datetime import datetime
import time


def get_available_dates(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT (ts::date)::text FROM ltp_ticks WHERE symbol = 'NIFTY 50' ORDER BY ts::date")
    return [row[0] for row in cursor.fetchall()]

def build_candles(df, timeframe_seconds):
    df['ts'] = pd.to_datetime(df['ts'], format='mixed')
    df = df.set_index('ts')
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles = candles.dropna()
    # 1: Green, -1: Red, 0: Doji
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
    return candles

def analyze_runs(candles):
    # Shift to compare with previous
    candles['prev_direction'] = candles['direction'].shift(1)
    
    # Identify Signals: Current matches Prev (and not Doji)
    # This is our entry point. We want to know what happens AFTER this.
    signal_mask = (candles['direction'] != 0) & (candles['direction'] == candles['prev_direction'])
    
    # We need to iterate to find run lengths efficiently
    # Converting to numpy for speed
    directions = candles['direction'].values
    indices = np.where(signal_mask)[0]
    
    run_lengths = []
    
    for idx in indices:
        current_dir = directions[idx]
        run = 0
        
        # Look ahead
        next_idx = idx + 1
        while next_idx < len(directions):
            if directions[next_idx] == current_dir:
                run += 1
                next_idx += 1
            else:
                break
        
        run_lengths.append(run)
    
    return run_lengths

def process_all_dates():
    start_time = time.time()
    conn = db.get_connection()
    dates = get_available_dates(conn)
    
    print(f"Found {len(dates)} dates to process.")
    
    results = {
        '20s': [],
        '30s': []
    }
    
    for date in dates:
        print(f"Processing {date}...")
        try:
            query = f"SELECT ts, ltp FROM ltp_ticks WHERE symbol = 'NIFTY 50' AND ts::date = '{date}'"
            df = pd.read_sql_query(query, conn)
            
            if len(df) < 100:
                continue
                
            # 20s Analysis
            c20 = build_candles(df, 20)
            runs20 = analyze_runs(c20)
            results['20s'].extend(runs20)
            
            # 30s Analysis
            c30 = build_candles(df, 30)
            runs30 = analyze_runs(c30)
            results['30s'].extend(runs30)
            
        except Exception as e:
            print(f"\nError processing {date}: {e}")
            
    conn.close()
    print(f"\nTime taken: {time.time() - start_time:.2f} seconds")
    
    return results

def print_stats(run_data, label):
    if not run_data:
        print(f"\nNo data for {label}")
        return

    series = pd.Series(run_data)
    total_signals = len(series)
    max_run = series.max()
    
    print(f"\n{'='*20} {label} ANALYSIS {'='*20}")
    print(f"Total Continuation Signals: {total_signals:,}")
    print(f"Maximum Consecutive Candles AFTER Signal: {max_run}")
    
    print("\n--- Run Length Distribution ---")
    counts = series.value_counts().sort_index()
    
    # Calculate cumulative retention (probability of run continuing)
    # Count of runs >= N
    at_least_n = {}
    for i in range(max_run + 2):
        at_least_n[i] = len(series[series >= i])
    
    print(f"{'Run Length':<12} | {'Count':<10} | {'% Occur':<10} | {'Probability of Reaching This'}")
    print("-" * 60)
    
    # We are analyzing "Future" candles. 0 means it reversed immediately.
    # 1 means we got 1 more candle, etc.
    # So "Probability of Reaching This" means: Given we had a signal (2 candles same dir),
    # what is chance we get N MORE candles?
    
    for length in range(min(10, max_run + 1)):
        count = counts.get(length, 0)
        pct = (count / total_signals) * 100
        cum_prob = (at_least_n[length] / total_signals) * 100
        
        # conditional prob: if we reached length-1, chance of reaching length?
        cond_prob = 0
        if length > 0 and at_least_n[length-1] > 0:
            cond_prob = (at_least_n[length] / at_least_n[length-1]) * 100
        elif length == 0:
            cond_prob = 100 # We always "reach" 0 (immediate reversal is a result)
            
        print(f"{length:<12} | {count:<10,} | {pct:<10.1f}% | {cum_prob:<10.1f}% (Cond: {cond_prob:.1f}%)")

if __name__ == "__main__":
    results = process_all_dates()
    print_stats(results['20s'], "20-SECOND CANDLES")
    print_stats(results['30s'], "30-SECOND CANDLES")
