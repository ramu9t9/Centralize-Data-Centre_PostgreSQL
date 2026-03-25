"""
FINAL FIX: Options Greeks Analysis
- Uses T separator (database format)
- Checks actual data availability
"""
import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

def build_candles(df, timeframe_seconds):
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.set_index('ts')
    candles = df['ltp'].resample(f'{timeframe_seconds}S').ohlc()
    candles = candles.dropna()
    candles['direction'] = np.where(candles['close'] > candles['open'], 1,
                           np.where(candles['close'] < candles['open'], -1, 0))
    return candles

def detect_signals(candles, lookforward=2):
    signals = []
    for i in range(len(candles) - lookforward):
        current_dir = candles.iloc[i]['direction']
        if current_dir == 0:
            continue
        next_dirs = [candles.iloc[i+j]['direction'] for j in range(1, lookforward+1)]
        if all(d == current_dir for d in next_dirs):
            signals.append({
                'timestamp': candles.index[i],
                'direction': 'BULLISH' if current_dir == 1 else 'BEARISH',
                'entry_price': candles.iloc[i]['close'],
            })
    return pd.DataFrame(signals)

def backtest_signal(signal, spot_df):
    entry_price = signal['entry_price']
    entry_time = pd.to_datetime(signal['timestamp'])
    direction = 1 if signal['direction'] == 'BULLISH' else -1
    
    spot_df_copy = spot_df.copy()
    spot_df_copy['ts'] = pd.to_datetime(spot_df_copy['ts'])
    future_data = spot_df_copy[spot_df_copy['ts'] > entry_time].head(12)
    
    if len(future_data) == 0:
        return None
    
    stop_pct = 0.1
    stop_loss = entry_price * (1 - (stop_pct/100) * direction)
    
    for idx, row in future_data.iterrows():
        price = row['ltp']
        if (direction == 1 and price <= stop_loss) or (direction == -1 and price >= stop_loss):
            pnl = ((stop_loss - entry_price) / entry_price) * 100 * direction
            return {'pnl_pct': pnl, 'win': False}
    
    exit_price = future_data.iloc[-1]['ltp']
    pnl = ((exit_price - entry_price) / entry_price) * 100 * direction
    return {'pnl_pct': pnl, 'win': pnl > 0}

print("=" * 80)
print("OPTIONS GREEKS ANALYSIS - FINAL FIX")
print("=" * 80)

conn = db.get_connection()

spot_df = pd.read_sql_query("""
    SELECT ts, ltp, volume FROM ltp_ticks
    WHERE symbol = 'NIFTY 50' AND ts::date = '2025-10-17'
    ORDER BY ts
""", conn)

print(f"\nAnalyzing: Oct 17, 2025 ({len(spot_df):,} ticks)")

# Build candles and detect signals
candles = build_candles(spot_df.copy(), 20)
signals = detect_signals(candles, lookforward=2)

print(f"Signals detected: {len(signals)}")

# Get expiry
cursor = conn.cursor()
cursor.execute("""
    SELECT DISTINCT substring(symbol from 6 for 7) as expiry
    FROM ltp_ticks
    WHERE symbol LIKE 'NIFTY%%'
      AND symbol != 'NIFTY 50'
      AND symbol NOT LIKE '%%FUT'
      AND (symbol LIKE '%CE' OR symbol LIKE '%PE')
      AND ts::date = '2025-10-17'
    ORDER BY expiry
    LIMIT 1
""")
expiry_result = cursor.fetchone()
expiry = expiry_result[0] if expiry_result else None

if not expiry:
    print("⚠️ No expiry found")
    conn.close()
    exit(1)

print(f"Using expiry: {expiry}")

enriched = []
failed_count = 0

for idx, signal in signals.iterrows():
    ts = pd.to_datetime(signal['timestamp'])
    # CRITICAL: Use T separator to match database format
    ts_str = ts.strftime('%Y-%m-%dT%H:%M:%S')
    
    atm_strike = int(round(signal['entry_price'] / 50) * 50)
    
    ce_symbol = f"NIFTY{expiry}{atm_strike}CE"
    pe_symbol = f"NIFTY{expiry}{atm_strike}PE"
    
    # Get CE data - use REPLACE to handle T separator
    ce_query = f"""
        SELECT ltp, delta, gamma, theta, vega, iv, bid, ask
        FROM ltp_ticks
        WHERE symbol = '{ce_symbol}'
          AND ts >= REPLACE(datetime('{ts_str}', '-5 minutes'), ' ', 'T')
          AND ts <= REPLACE(datetime('{ts_str}', '+5 minutes'), ' ', 'T')
        ORDER BY ABS(julianday(REPLACE(ts, 'T', ' ')) - julianday(REPLACE('{ts_str}', 'T', ' ')))
        LIMIT 1
    """
    ce_data = pd.read_sql_query(ce_query, conn)
    
    # Get PE data
    pe_query = f"""
        SELECT ltp, delta, gamma, theta, vega, iv, bid, ask
        FROM ltp_ticks
        WHERE symbol = '{pe_symbol}'
          AND ts >= REPLACE(datetime('{ts_str}', '-5 minutes'), ' ', 'T')
          AND ts <= REPLACE(datetime('{ts_str}', '+5 minutes'), ' ', 'T')
        ORDER BY ABS(julianday(REPLACE(ts, 'T', ' ')) - julianday(REPLACE('{ts_str}', 'T', ' ')))
        LIMIT 1
    """
    pe_data = pd.read_sql_query(pe_query, conn)
    
    result = backtest_signal(signal, spot_df)
    
    if result and len(ce_data) > 0 and len(pe_data) > 0:
        signal_dict = signal.to_dict()
        signal_dict.update(result)
        signal_dict['atm_strike'] = atm_strike
        
        ce = ce_data.iloc[0]
        signal_dict['ce_ltp'] = ce['ltp']
        signal_dict['ce_delta'] = ce['delta']
        signal_dict['ce_gamma'] = ce['gamma']
        signal_dict['ce_theta'] = ce['theta']
        signal_dict['ce_vega'] = ce['vega']
        signal_dict['ce_iv'] = ce['iv']
        
        pe = pe_data.iloc[0]
        signal_dict['pe_ltp'] = pe['ltp']
        signal_dict['pe_delta'] = pe['delta']
        signal_dict['pe_gamma'] = pe['gamma']
        signal_dict['pe_theta'] = pe['theta']
        signal_dict['pe_vega'] = pe['vega']
        signal_dict['pe_iv'] = pe['iv']
        
        signal_dict['iv_skew'] = pe['iv'] - ce['iv']
        
        enriched.append(signal_dict)
    else:
        failed_count += 1

conn.close()

print(f"\nEnriched: {len(enriched)} signals")
print(f"Failed: {failed_count}")

if len(enriched) == 0:
    print("\n⚠️ No data - options may not exist for Dec 30")
    exit(1)

df = pd.DataFrame(enriched)
winners = df[df['win'] == True]
losers = df[df['win'] == False]

print("\n" + "=" * 80)
print(f"Winners: {len(winners)} ({len(winners)/len(df)*100:.1f}%)")
print(f"Losers: {len(losers)} ({len(losers)/len(df)*100:.1f}%)")

if len(winners) > 0 and len(losers) > 0:
    print("\nATM CE Delta:")
    print(f"  Winners: {winners['ce_delta'].mean():.4f}")
    print(f"  Losers:  {losers['ce_delta'].mean():.4f}")
    
    print("\nATM CE IV:")
    print(f"  Winners: {winners['ce_iv'].mean():.2f}%")
    print(f"  Losers:  {losers['ce_iv'].mean():.2f}%")
    
    print("\nIV Skew:")
    print(f"  Winners: {winners['iv_skew'].mean():.2f}%")
    print(f"  Losers:  {losers['iv_skew'].mean():.2f}%")
    
    df.to_csv('greeks_results.csv', index=False)
    print("\n📊 Saved: greeks_results.csv")
else:
    print("\n⚠️ Not enough data for comparison")

print("=" * 80)
