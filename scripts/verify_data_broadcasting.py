#!/usr/bin/env python3
"""
Verify that data broadcasting is working correctly (PostgreSQL)
- Check if sync/collector is writing to database
- Check if broadcaster is reading from database
- Check if clients are receiving data
"""

import sys
import asyncio
import websockets
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

WS_URL = "ws://localhost:8765"

def check_database():
    """Check PostgreSQL for recent records"""
    print("=" * 70)
    print("DATABASE VERIFICATION (PostgreSQL)")
    print("=" * 70)
    print()
    
    try:
        conn = db.get_connection()
        if not db.table_exists(conn, "ltp_ticks"):
            conn.close()
            print("❌ Table ltp_ticks not found")
            return False
        print("✅ Connected to PostgreSQL")
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
        total_count = cursor.fetchone()[0]
        print(f"📊 Total records: {total_count:,}")
        
        cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
        latest_ts = cursor.fetchone()[0]
        if latest_ts is not None:
            latest_str = str(latest_ts)
            print(f"📅 Latest record: {latest_str}")
            try:
                if hasattr(latest_ts, 'timestamp'):
                    latest_dt = latest_ts.replace(tzinfo=timezone.utc) if latest_ts.tzinfo is None else latest_ts
                else:
                    latest_dt = datetime.fromisoformat(latest_str.replace('Z', '+00:00'))
                    latest_dt = latest_dt.replace(tzinfo=timezone.utc) if latest_dt.tzinfo is None else latest_dt
                now = datetime.now(timezone.utc)
                age = (now - latest_dt).total_seconds()
                if age < 300:
                    print(f"✅ Data is recent ({int(age)} seconds old)")
                else:
                    print(f"⚠️  Data is old ({int(age/60)} minutes old)")
            except Exception:
                pass
        
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        cursor.execute("SELECT COUNT(*) FROM ltp_ticks WHERE ts > %s::timestamptz", (five_min_ago,))
        recent_count = cursor.fetchone()[0]
        print(f"📈 Records in last 5 minutes: {recent_count:,}")
        
        cursor.execute("SELECT COUNT(DISTINCT symbol) FROM ltp_ticks")
        symbol_count = cursor.fetchone()[0]
        print(f"🔖 Unique symbols: {symbol_count}")
        
        cursor.execute("""
            SELECT symbol, ts, ltp, volume, oi, delta, iv 
            FROM ltp_ticks 
            WHERE ts > %s::timestamptz 
            ORDER BY ts DESC 
            LIMIT 5
        """, (five_min_ago,))
        recent_records = cursor.fetchall()
        if recent_records:
            print()
            print("📋 Sample recent records:")
            for record in recent_records:
                symbol, ts, ltp, volume, oi, delta, iv = record
                print(f"   {symbol}: LTP={ltp}, Volume={volume}, OI={oi}, Delta={delta}, IV={iv}")
                print(f"      Time: {ts}")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error reading database: {e}")
        return False


async def check_websocket_service():
    """Check if WebSocket service is running and broadcasting"""
    print()
    print("=" * 70)
    print("WEBSOCKET SERVICE VERIFICATION")
    print("=" * 70)
    print()
    
    try:
        async with websockets.connect(WS_URL) as websocket:
            print(f"✅ Connected to WebSocket service: {WS_URL}")
            
            # Wait for welcome message
            try:
                welcome = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                welcome_data = json.loads(welcome)
                print(f"✅ Welcome message received: {welcome_data.get('message')}")
                print(f"   Subscribers: {welcome_data.get('subscribers')}")
            except asyncio.TimeoutError:
                print("⚠️  No welcome message received")
            
            # Listen for data for 15 seconds
            print()
            print("📊 Listening for broadcasted data (15 seconds)...")
            print()
            
            records_received = 0
            symbols_seen = set()
            start_time = asyncio.get_event_loop().time()
            
            while (asyncio.get_event_loop().time() - start_time) < 15:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)
                    
                    # Skip welcome/ping messages
                    if data.get('type') in ['welcome', 'pong']:
                        continue
                    
                    records_received += 1
                    symbol = data.get('symbol', 'Unknown')
                    symbols_seen.add(symbol)
                    
                    # Show first 5 records
                    if records_received <= 5:
                        print(f"📨 Record #{records_received}: {symbol}")
                        print(f"   LTP: {data.get('ltp')}, Volume: {data.get('volume')}, OI: {data.get('oi')}")
                        print(f"   Delta: {data.get('delta')}, IV: {data.get('iv')}")
                        print(f"   Time: {data.get('ts')}")
                        print()
                except asyncio.TimeoutError:
                    # No message received, continue waiting
                    continue
                except Exception as e:
                    print(f"⚠️  Error receiving message: {e}")
                    break
            
            print("=" * 70)
            print(f"✅ Received {records_received} records during test")
            print(f"   Unique symbols: {len(symbols_seen)}")
            print(f"   Sample symbols: {list(symbols_seen)[:10]}")
            
            if records_received > 0:
                print()
                print("✅ SUCCESS: Data broadcasting is working!")
            else:
                print()
                print("⚠️  WARNING: No data received during test")
                print("   Check if VPS collector is writing new records")
            
            return records_received > 0
            
    except ConnectionRefusedError:
        print(f"❌ Connection refused: {WS_URL}")
        print("   Make sure WebSocket Broadcaster Service is running:")
        print("   py websocket_broadcaster_service.py")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Main verification function"""
    print()
    print("=" * 70)
    print("DATA BROADCASTING VERIFICATION")
    print("=" * 70)
    print()
    
    # Check database
    db_ok = check_database()
    
    if not db_ok:
        print()
        print("❌ Database check failed. Cannot proceed with WebSocket test.")
        return
    
    # Check WebSocket service
    ws_ok = asyncio.run(check_websocket_service())
    
    # Final summary
    print()
    print("=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    print()
    print(f"Database: {'✅ OK' if db_ok else '❌ FAILED'}")
    print(f"WebSocket Service: {'✅ OK' if ws_ok else '❌ FAILED'}")
    print()
    
    if db_ok and ws_ok:
        print("✅ ALL CHECKS PASSED - Data broadcasting is working!")
    else:
        print("⚠️  SOME CHECKS FAILED - Review the output above")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nVerification stopped by user")

