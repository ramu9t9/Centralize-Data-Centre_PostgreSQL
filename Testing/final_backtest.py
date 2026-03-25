"""
Final Multi-Day Scalping Strategy Backtest
------------------------------------------
Timeframe: 20 Seconds
Filters:
1. Body Size > 0.0027%
2. Wick Ratio < 1.43
3. Momentum = 1 (Continuation)
4. Trading Hours: 04:00, 06:00, 08:00 UTC (approx)
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

# --- Configuration ---
TIMEFRAME = 20  # seconds
BODY_THRESHOLD = 0.0027
WICK_RATIO_THRESHOLD = 1.43
TRADING_HOURS = [4, 6, 8]  # UTC hours to trade
STOP_LOSS_PCT = 0.05  # Tight stop for scalping
TAKE_PROFIT_PCT = 0.10 # Target

def build_candles(df, timeframe_seconds):
    df['ts'] = pd.to_datetime(df['ts'], format='mixed')
    df = df.set_index('ts')
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles = candles.dropna()
    
    # Calculatemetrics
    candles['body_size'] = abs(candles['close'] - candles['open'])
    candles['body_pct'] = (candles['body_size'] / candles['open']) * 100
    
    candles['upper_wick'] = np.where(candles['close'] > candles['open'], 
                                   candles['high'] - candles['close'],
                                   candles['high'] - candles['open'])
    candles['lower_wick'] = np.where(candles['close'] > candles['open'], 
                                   candles['open'] - candles['low'],
                                   candles['close'] - candles['low'])
    
    candles['total_wick'] = candles['upper_wick'] + candles['lower_wick']
    # Avoid division by zero
    candles['wick_ratio'] = np.where(candles['body_size'] > 0, 
                                   candles['total_wick'] / candles['body_size'], 
                                   100) # Penalize dojis
                                   
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
                           
    return candles

def backtest_signal(entry_idx, candles, direction, sl_pct, tp_pct):
    entry_price = candles.iloc[entry_idx]['close']
    stop_loss = entry_price * (1 - sl_pct/100) if direction == 1 else entry_price * (1 + sl_pct/100)
    take_profit = entry_price * (1 + tp_pct/100) if direction == 1 else entry_price * (1 - tp_pct/100)
    
    # Look ahead up to 12 candles (4 minutes)
    for i in range(1, 13):
        if entry_idx + i >= len(candles):
            break
            
        future_candle = candles.iloc[entry_idx + i]
        
        # Check High/Low for hits
        if direction == 1: # Long
            if future_candle['low'] <= stop_loss:
                return -sl_pct
            if future_candle['high'] >= take_profit:
                return tp_pct
        else: # Short
            if future_candle['high'] >= stop_loss:
                return -sl_pct
            if future_candle['low'] <= take_profit:
                return tp_pct
                
    # Time exit
    exit_price = candles.iloc[min(entry_idx + 12, len(candles)-1)]['close']
    pnl = ((exit_price - entry_price) / entry_price) * 100 * direction
    return pnl

def run_backtest():
    start_time = time.time()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT (ts::date)::text FROM ltp_ticks WHERE symbol = 'NIFTY 50' ORDER BY ts::date")
    dates = [row[0] for row in cursor.fetchall()]
    
    print(f"Backtesting {len(dates)} days with 20s timeframe...")
    print(f"Filters: Body > {BODY_THRESHOLD}%, Wick Ratio < {WICK_RATIO_THRESHOLD}, Hours: {TRADING_HOURS}")
    
    total_trades = 0
    total_wins = 0
    total_pnl = 0.0
    
    daily_results = []
    
    for date in dates:
        try:
            query = f"SELECT ts, ltp FROM ltp_ticks WHERE symbol = 'NIFTY 50' AND ts::date = '{date}'"
            df = pd.read_sql_query(query, conn)
            
            if len(df) < 100:
                continue
                
            candles = build_candles(df, TIMEFRAME)
            candles['hour'] = candles.index.hour
            candles['prev_direction'] = candles['direction'].shift(1)
            
            # Identify Signals
            # 1. Momentum (Same Direction)
            # 2. Body Size Filter
            # 3. Wick Ratio Filter
            # 4. Time Filter
            
            mask = (
                (candles['direction'] != 0) & 
                (candles['direction'] == candles['prev_direction']) &
                (candles['body_pct'] > BODY_THRESHOLD) &
                (candles['wick_ratio'] < WICK_RATIO_THRESHOLD) &
                (candles['hour'].isin(TRADING_HOURS))
            )
            
            signal_indices = np.where(mask)[0]
            
            day_trades = 0
            day_wins = 0
            day_pnl = 0.0
            
            for idx in signal_indices:
                # Skip if close to end
                if idx >= len(candles) - 12:
                    continue
                    
                direction = candles.iloc[idx]['direction']
                pnl = backtest_signal(idx, candles, direction, STOP_LOSS_PCT, TAKE_PROFIT_PCT)
                
                day_trades += 1
                day_pnl += pnl
                if pnl > 0:
                    day_wins += 1
            
            if day_trades > 0:
                win_rate = (day_wins / day_trades) * 100
                daily_results.append({
                    'date': date,
                    'trades': day_trades,
                    'wins': day_wins,
                    'win_rate': win_rate,
                    'pnl': day_pnl
                })
                
                total_trades += day_trades
                total_wins += day_wins
                total_pnl += day_pnl
                
            print(f"  {date}: {day_trades} trades, WR: {win_rate:.1f}% ({day_wins}/{day_trades})")
            
        except Exception as e:
            print(f"Error on {date}: {e}")
            
    conn.close()
    
    # Final Stats
    print("\n" + "="*60)
    print("FINAL BACKTEST RESULTS (20s Timeframe)")
    print("="*60)
    
    print(f"Total Days Traded: {len(daily_results)}")
    print(f"Total Trades: {total_trades}")
    if total_trades > 0:
        overall_wr = (total_wins / total_trades) * 100
        avg_pnl = total_pnl / total_trades
        
        print(f"Overall Win Rate: {overall_wr:.2f}%")
        print(f"Average PnL per Trade: {avg_pnl:.4f}%")
        print(f"Total PnL (uncompounded): {total_pnl:.2f}%")
        
        # Best/Worst Days
        df_res = pd.DataFrame(daily_results)
        best_day = df_res.loc[df_res['pnl'].idxmax()]
        worst_day = df_res.loc[df_res['pnl'].idxmin()]
        
        print(f"\nBest Day: {best_day['date']} (+{best_day['pnl']:.2f}%)")
        print(f"Worst Day: {worst_day['date']} ({worst_day['pnl']:.2f}%)")
        
        # Consistency
        profitable_days = len(df_res[df_res['pnl'] > 0])
        print(f"\nProfitable Days: {profitable_days}/{len(daily_results)} ({profitable_days/len(daily_results)*100:.1f}%)")

    print(f"\nTime taken: {time.time() - start_time:.2f} seconds")

if __name__ == "__main__":
    run_backtest()
