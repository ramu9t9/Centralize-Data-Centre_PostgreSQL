"""
Greeks & IV Analysis - Find Optimal Ranges for Signal Quality

Analyzes futures and ATM options data at signal times to identify:
1. Futures Greeks ranges for winners vs losers
2. ATM Options IV ranges
3. IV skew patterns (PE IV - CE IV)
4. Delta patterns
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db
import pandas as pd
import numpy as np


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
print("GREEKS & IV ANALYSIS - Optimal Ranges for Signal Quality")
print("=" * 80)

conn = sqlite3.connect(str(db_path))

# Load spot data
spot_df = pd.read_sql_query("""
    SELECT ts, ltp, volume FROM ltp_ticks
    WHERE symbol = 'NIFTY 50' AND ts::date = '2025-12-30'
    ORDER BY ts
""", conn)

print(f"\nLoading data for Dec 30, 2025...")

# Build candles and detect signals
candles = build_candles(spot_df.copy(), 20)
signals = detect_signals(candles, lookforward=2)

print(f"Signals detected: {len(signals)}")

# Get futures symbol for this date
cursor = conn.cursor()
cursor.execute("""
    SELECT DISTINCT symbol FROM ltp_ticks 
    WHERE symbol LIKE '%%FUT' AND ts::date = '2025-12-30'
    LIMIT 1
""")
futures_symbol = cursor.fetchone()
futures_symbol = futures_symbol[0] if futures_symbol else None

print(f"Futures symbol: {futures_symbol}")

# Enrich signals with Greeks data
print("\nEnriching signals with Greeks & IV data...")
enriched = []

for idx, signal in signals.iterrows():
    ts = pd.to_datetime(signal['timestamp'])
    ts_str = ts.strftime('%Y-%m-%d %H:%M:%S')
    
    # Get futures data (within 30 second window)
    if futures_symbol:
        futures_query = f"""
            SELECT ltp, delta, gamma, theta, vega, iv, bid, ask
            FROM ltp_ticks
            WHERE symbol = '{futures_symbol}'
              AND ts BETWEEN datetime('{ts_str}', '-30 seconds') 
                         AND datetime('{ts_str}', '+30 seconds')
            ORDER BY ABS(julianday(ts) - julianday('{ts_str}'))
            LIMIT 1
        """
        futures_data = pd.read_sql_query(futures_query, conn)
    else:
        futures_data = pd.DataFrame()
    
    # Get ATM options (CE and PE)
    atm_strike = round(signal['entry_price'] / 50) * 50
    
    options_query = f"""
        SELECT symbol, ltp, delta, gamma, theta, vega, iv, bid, ask
        FROM ltp_ticks
        WHERE (symbol LIKE '%{int(atm_strike)}CE' OR symbol LIKE '%{int(atm_strike)}PE')
          AND ts BETWEEN datetime('{ts_str}', '-30 seconds') 
                     AND datetime('{ts_str}', '+30 seconds')
        ORDER BY ABS(julianday(ts) - julianday('{ts_str}'))
        LIMIT 2
    """
    options_data = pd.read_sql_query(options_query, conn)
    
    # Backtest
    result = backtest_signal(signal, spot_df)
    
    if result:
        signal_dict = signal.to_dict()
        signal_dict.update(result)
        
        # Add futures data
        if len(futures_data) > 0:
            fut = futures_data.iloc[0]
            signal_dict['fut_delta'] = fut['delta']
            signal_dict['fut_gamma'] = fut['gamma']
            signal_dict['fut_theta'] = fut['theta']
            signal_dict['fut_vega'] = fut['vega']
            signal_dict['fut_iv'] = fut['iv']
            signal_dict['fut_spread'] = ((fut['ask'] - fut['bid']) / fut['ltp'] * 100) if fut['bid'] > 0 else None
        
        # Add options data
        if len(options_data) > 0:
            ce_data = options_data[options_data['symbol'].str.contains('CE')]
            pe_data = options_data[options_data['symbol'].str.contains('PE')]
            
            if len(ce_data) > 0:
                ce = ce_data.iloc[0]
                signal_dict['ce_delta'] = ce['delta']
                signal_dict['ce_iv'] = ce['iv']
                signal_dict['ce_spread'] = ((ce['ask'] - ce['bid']) / ce['ltp'] * 100) if ce['bid'] > 0 else None
            
            if len(pe_data) > 0:
                pe = pe_data.iloc[0]
                signal_dict['pe_delta'] = pe['delta']
                signal_dict['pe_iv'] = pe['iv']
                signal_dict['pe_spread'] = ((pe['ask'] - pe['bid']) / pe['ltp'] * 100) if pe['bid'] > 0 else None
            
            # Calculate IV skew
            if len(ce_data) > 0 and len(pe_data) > 0:
                signal_dict['iv_skew'] = pe_data.iloc[0]['iv'] - ce_data.iloc[0]['iv']
        
        enriched.append(signal_dict)

conn.close()

df = pd.DataFrame(enriched)
print(f"Enriched: {len(df)} signals with Greeks data")

# Check what columns we have
print(f"\nAvailable columns: {df.columns.tolist()}")

# Filter to only signals with complete data (check which columns exist)
required_cols = []
for col in ['fut_delta', 'fut_iv', 'ce_iv', 'pe_iv']:
    if col in df.columns:
        required_cols.append(col)

if len(required_cols) > 0:
    complete_df = df.dropna(subset=required_cols)
    print(f"Complete data: {len(complete_df)} signals (with {required_cols})")
else:
    complete_df = df
    print(f"No Greeks columns found - using all {len(df)} signals")

if len(complete_df) > 0:
    winners = complete_df[complete_df['win'] == True]
    losers = complete_df[complete_df['win'] == False]
    
    print("\n" + "=" * 80)
    print("GREEKS & IV ANALYSIS - Winners vs Losers")
    print("=" * 80)
    
    print(f"\nWinners: {len(winners)} | Losers: {len(losers)}")
    
    # Futures Greeks
    print("\n1. FUTURES GREEKS:")
    
    if 'fut_delta' in complete_df.columns:
        print(f"\n   Delta:")
        print(f"      Winners: {winners['fut_delta'].mean():.4f} ± {winners['fut_delta'].std():.4f}")
        print(f"      Losers:  {losers['fut_delta'].mean():.4f} ± {losers['fut_delta'].std():.4f}")
        print(f"      Range (Winners): [{winners['fut_delta'].quantile(0.25):.4f}, {winners['fut_delta'].quantile(0.75):.4f}]")
    
    if 'fut_gamma' in complete_df.columns:
        print(f"\n   Gamma:")
        print(f"      Winners: {winners['fut_gamma'].mean():.6f} ± {winners['fut_gamma'].std():.6f}")
        print(f"      Losers:  {losers['fut_gamma'].mean():.6f} ± {losers['fut_gamma'].std():.6f}")
    
    if 'fut_theta' in complete_df.columns:
        print(f"\n   Theta:")
        print(f"      Winners: {winners['fut_theta'].mean():.4f} ± {winners['fut_theta'].std():.4f}")
        print(f"      Losers:  {losers['fut_theta'].mean():.4f} ± {losers['fut_theta'].std():.4f}")
    
    if 'fut_vega' in complete_df.columns:
        print(f"\n   Vega:")
        print(f"      Winners: {winners['fut_vega'].mean():.4f} ± {winners['fut_vega'].std():.4f}")
        print(f"      Losers:  {losers['fut_vega'].mean():.4f} ± {losers['fut_vega'].std():.4f}")
    
    if 'fut_iv' in complete_df.columns:
        print(f"\n   IV (Futures):")
        print(f"      Winners: {winners['fut_iv'].mean():.2f}% ± {winners['fut_iv'].std():.2f}%")
        print(f"      Losers:  {losers['fut_iv'].mean():.2f}% ± {losers['fut_iv'].std():.2f}%")
        print(f"      Range (Winners): [{winners['fut_iv'].quantile(0.25):.2f}%, {winners['fut_iv'].quantile(0.75):.2f}%]")
    
    # Options IV
    print("\n2. ATM OPTIONS IV:")
    
    if 'ce_iv' in complete_df.columns:
        print(f"\n   CE IV:")
        print(f"      Winners: {winners['ce_iv'].mean():.2f}% ± {winners['ce_iv'].std():.2f}%")
        print(f"      Losers:  {losers['ce_iv'].mean():.2f}% ± {losers['ce_iv'].std():.2f}%")
        print(f"      Range (Winners): [{winners['ce_iv'].quantile(0.25):.2f}%, {winners['ce_iv'].quantile(0.75):.2f}%]")
    
    if 'pe_iv' in complete_df.columns:
        print(f"\n   PE IV:")
        print(f"      Winners: {winners['pe_iv'].mean():.2f}% ± {winners['pe_iv'].std():.2f}%")
        print(f"      Losers:  {losers['pe_iv'].mean():.2f}% ± {losers['pe_iv'].std():.2f}%")
        print(f"      Range (Winners): [{winners['pe_iv'].quantile(0.25):.2f}%, {winners['pe_iv'].quantile(0.75):.2f}%]")
    
    if 'iv_skew' in complete_df.columns:
        print(f"\n   IV Skew (PE - CE):")
        print(f"      Winners: {winners['iv_skew'].mean():.2f}% ± {winners['iv_skew'].std():.2f}%")
        print(f"      Losers:  {losers['iv_skew'].mean():.2f}% ± {losers['iv_skew'].std():.2f}%")
        print(f"      Range (Winners): [{winners['iv_skew'].quantile(0.25):.2f}%, {winners['iv_skew'].quantile(0.75):.2f}%]")
    
    # ATM Delta
    print("\n3. ATM OPTIONS DELTA:")
    
    if 'ce_delta' in complete_df.columns:
        print(f"\n   CE Delta:")
        print(f"      Winners: {winners['ce_delta'].mean():.4f} ± {winners['ce_delta'].std():.4f}")
        print(f"      Losers:  {losers['ce_delta'].mean():.4f} ± {losers['ce_delta'].std():.4f}")
    
    if 'pe_delta' in complete_df.columns:
        print(f"\n   PE Delta:")
        print(f"      Winners: {winners['pe_delta'].mean():.4f} ± {winners['pe_delta'].std():.4f}")
        print(f"      Losers:  {losers['pe_delta'].mean():.4f} ± {losers['pe_delta'].std():.4f}")
    
    print("\n" + "=" * 80)
    print("RECOMMENDED RANGES FOR HIGH-QUALITY SIGNALS:")
    print("=" * 80)
    
    if 'fut_iv' in complete_df.columns:
        fut_iv_low = winners['fut_iv'].quantile(0.25)
        fut_iv_high = winners['fut_iv'].quantile(0.75)
        print(f"1. Futures IV: {fut_iv_low:.2f}% - {fut_iv_high:.2f}%")
    
    if 'ce_iv' in complete_df.columns:
        ce_iv_low = winners['ce_iv'].quantile(0.25)
        ce_iv_high = winners['ce_iv'].quantile(0.75)
        print(f"2. ATM CE IV: {ce_iv_low:.2f}% - {ce_iv_high:.2f}%")
    
    if 'pe_iv' in complete_df.columns:
        pe_iv_low = winners['pe_iv'].quantile(0.25)
        pe_iv_high = winners['pe_iv'].quantile(0.75)
        print(f"3. ATM PE IV: {pe_iv_low:.2f}% - {pe_iv_high:.2f}%")
    
    if 'iv_skew' in complete_df.columns:
        skew_low = winners['iv_skew'].quantile(0.25)
        skew_high = winners['iv_skew'].quantile(0.75)
        print(f"4. IV Skew: {skew_low:.2f}% - {skew_high:.2f}%")
    
    print("\n" + "=" * 80)
    
    # Save
    complete_df.to_csv('greeks_iv_analysis.csv', index=False)
    print("📊 Saved: greeks_iv_analysis.csv")
else:
    print("\n⚠️ No signals with complete Greeks data found")
    print("This might be due to:")
    print("  - Futures/options data not available for this date")
    print("  - Timestamp mismatch between spot and derivatives")
