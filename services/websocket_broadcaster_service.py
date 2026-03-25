#!/usr/bin/env python3
"""
WebSocket Broadcaster Service (PostgreSQL)
- Monitors local PostgreSQL for new records (synced from VPS)
- Broadcasts to WebSocket clients (ws://localhost:8765)
"""

import asyncio
import websockets
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Set, Dict, Any, Optional
import time

# Allow importing db when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

WS_HOST = "localhost"
WS_PORT = 8765
MONITOR_INTERVAL = 5  # Check database every 5 seconds
BATCH_SIZE = 100  # Maximum records to broadcast per cycle

# Setup logging - log file in project root/data/
log_file_path = Path(__file__).parent.parent / 'data' / 'broadcaster_service.log'
log_file_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(log_file_path), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class WebSocketBroadcaster:
    """
    Service that monitors database and broadcasts to WebSocket clients.
    Supports both LIVE mode (monitor new records) and REPLAY mode (historical data).
    Does NOT collect data - VPS collector does that.
    """
    
    def __init__(self, db_path: Optional[Path] = None, ws_host: str = "localhost", ws_port: int = 8765, auto_start: bool = False):
        self.db_path = db_path  # Kept for API compat; connection uses DATABASE_URL
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.subscribers: Set[websockets.WebSocketServerProtocol] = set()
        self.last_ts: Optional[str] = None
        self.running = False
        self.auto_start = auto_start  # Whether to start broadcasting automatically
        
        # Mode management (LIVE or REPLAY)
        self.mode = "LIVE"  # Default to LIVE mode
        self.mode_lock = threading.Lock()
        
        # Broadcasting state
        self.broadcasting_active = False  # Whether broadcasting has been started by user
        self.broadcasting_lock = threading.Lock()
        
        # Replay configuration
        self.replay_config = {
            'start_time': None,
            'end_time': None,
            'speed': 1.0,
            'paused': False,
            'current_position': 0,
            'total_records': 0,
            'replay_records': None,  # Cached records for replay
            'replay_index': 0,  # Current position in replay_records
            'current_timestamp': None,  # Current timestamp being broadcast
            'replay_event': threading.Event()  # For pause/resume control
        }
        self.replay_config_lock = threading.Lock()
        self.replay_config['replay_event'].set()  # Start unpaused
        
        # Progress callback (for GUI updates)
        self.progress_callback = None

        # Optional broadcast log callback (for GUI debug logging)
        # Called with a dict: {mode, ts, record_count, symbol_count, sample, subscribers}
        self.broadcast_log_callback = None

        # Track clients for diagnostics (why so many connections?)
        self._client_info = {}  # websocket -> {"addr": str, "ip": str, "connected_at": float}
        
        logger.info("WebSocket Broadcaster initialized (PostgreSQL)")
        logger.info(f"WebSocket: ws://{ws_host}:{ws_port}")
        logger.info(f"Mode: {self.mode}")
    
    def _row_to_record(self, cur, row) -> dict:
        """Convert psycopg2 row to record dict."""
        if row is None:
            return {}
        cols = [d[0] for d in cur.description]
        r = dict(zip(cols, row))
        return {
            'symbol': r.get('symbol'),
            'token': r.get('token'),
            'ts': str(r['ts']) if r.get('ts') is not None else None,
            'ltp': r.get('ltp'),
            'bid': r.get('bid'),
            'ask': r.get('ask'),
            'volume': r.get('volume'),
            'oi': r.get('oi'),
            'delta': r.get('delta'),
            'gamma': r.get('gamma'),
            'theta': r.get('theta'),
            'vega': r.get('vega'),
            'iv': r.get('iv'),
            'source': r.get('source') or 'api'
        }

    def get_new_records(self) -> list:
        """
        Get new records from PostgreSQL since last broadcast.
        Returns list of records as dictionaries.
        """
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                return []
            cursor = conn.cursor()
            
            if self.last_ts:
                query = """
                    SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                           delta, gamma, theta, vega, iv, source
                    FROM ltp_ticks 
                    WHERE ts > %s::timestamptz 
                    ORDER BY ts
                    LIMIT %s
                """
                cursor.execute(query, (self.last_ts, BATCH_SIZE))
            else:
                cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
                row = cursor.fetchone()
                max_ts = row[0] if row else None
                conn.close()
                if max_ts is not None:
                    self.last_ts = str(max_ts).replace(' ', 'T')[:19]
                logger.info(f"Live baseline established (last_ts={self.last_ts})")
                return []
            
            records = []
            max_ts = self.last_ts
            for row in cursor.fetchall():
                record = self._row_to_record(cursor, row)
                ts_val = record.get('ts')
                if ts_val and (not max_ts or ts_val > max_ts):
                    max_ts = ts_val
                records.append(record)
            if max_ts:
                self.last_ts = max_ts
            conn.close()
            if records:
                logger.debug(f"Retrieved {len(records)} new records (last_ts: {self.last_ts})")
            return records
        except Exception as e:
            logger.error(f"Error reading database: {e}")
            return []
    
    async def broadcast_record(self, record: Dict[str, Any]):
        """
        Broadcast a single record to all connected clients.
        """
        if not self.subscribers:
            return
        
        try:
            message = json.dumps(record)
            disconnected = set()

            # Send concurrently across clients (one send per connection).
            # This prevents N_clients from multiplying the broadcast interval.
            subs = list(self.subscribers)
            tasks = [asyncio.create_task(s.send(message)) for s in subs]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for s, res in zip(subs, results):
                if isinstance(res, websockets.exceptions.ConnectionClosed):
                    disconnected.add(s)
                elif isinstance(res, Exception):
                    disconnected.add(s)
            
            # Remove disconnected clients
            if disconnected:
                self.subscribers -= disconnected
                for s in disconnected:
                    self._client_info.pop(s, None)
                logger.info(f"Removed {len(disconnected)} disconnected client(s). Active: {len(self.subscribers)}")
        
        except Exception as e:
            logger.error(f"Error in broadcast_record: {e}")
    
    async def broadcast_records(self, records: list):
        """
        Broadcast multiple records to all connected clients.
        """
        if not records:
            return

        # Emit debug log even if there are 0 subscribers (testing/verification mode).
        # This answers: "what data would be broadcast at what timestamp?"
        if self.broadcast_log_callback:
            try:
                ts = None
                try:
                    ts = records[0].get("ts") if isinstance(records[0], dict) else None
                except Exception:
                    ts = None

                mode = self.get_mode()
                clients = len(self.subscribers)

                # Emit structured rows for UI table (one row per record).
                # Keep payload lightweight: only key fields.
                rows = []
                for r in records:
                    if not isinstance(r, dict):
                        continue
                    rows.append({
                        "ts": r.get("ts") or ts,
                        "mode": mode,
                        "symbol": r.get("symbol"),
                        "ltp": r.get("ltp"),
                        "volume": r.get("volume"),
                        "oi": r.get("oi"),
                        "iv": r.get("iv"),
                        "source": r.get("source"),
                        "clients": clients,
                    })

                symbol_count = 0
                try:
                    symbol_count = len({r.get("symbol") for r in records if isinstance(r, dict) and r.get("symbol")})
                except Exception:
                    symbol_count = 0

                self.broadcast_log_callback({
                    "mode": mode,
                    "ts": ts,
                    "record_count": len(records),
                    "symbol_count": symbol_count,
                    "rows": rows,
                    "subscribers": clients,
                })
            except Exception:
                pass

        if not self.subscribers:
            return
        
        broadcast_count = 0
        for record in records:
            await self.broadcast_record(record)
            broadcast_count += 1
        
        if broadcast_count > 0:
            logger.info(f"Broadcasted {broadcast_count} records to {len(self.subscribers)} client(s)")

    def set_broadcast_log_callback(self, callback):
        """Set callback for broadcast debug logs."""
        self.broadcast_log_callback = callback
    
    async def websocket_handler(self, websocket):
        """
        Handle WebSocket client connections.
        """
        # Add new subscriber
        self.subscribers.add(websocket)
        try:
            client_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        except:
            client_addr = "unknown"
        try:
            ip_only = websocket.remote_address[0] if websocket.remote_address else "unknown"
        except Exception:
            ip_only = "unknown"

        self._client_info[websocket] = {
            "addr": client_addr,
            "ip": ip_only,
            "connected_at": time.time(),
        }

        # Log per-IP counts to help identify reconnection storms / multiple clients
        try:
            counts = {}
            for info in self._client_info.values():
                ip = info.get("ip", "unknown")
                counts[ip] = counts.get(ip, 0) + 1
            top = ", ".join([f"{ip}={n}" for ip, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:5]])
        except Exception:
            top = ""
        logger.info(f"New client connected: {client_addr} (Total: {len(self.subscribers)})")
        if top:
            logger.info(f"Client IP counts: {top}")
        
        try:
            # Send welcome message
            welcome = {
                'type': 'welcome',
                'message': 'Connected to Real-Time Data Centre',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'subscribers': len(self.subscribers)
            }
            await websocket.send(json.dumps(welcome))
            
            # Keep connection alive and handle any incoming messages
            async for message in websocket:
                try:
                    request = json.loads(message)
                    logger.debug(f"Received request from {client_addr}: {request}")
                    
                    # Handle client requests
                    if request.get('type') == 'ping':
                        # Respond to ping
                        response = {
                            'type': 'pong',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        await websocket.send(json.dumps(response))
                    
                    elif request.get('type') == 'get_latest':
                        # Send latest record for a symbol
                        symbol = request.get('symbol')
                        if symbol:
                            latest = self.get_latest_for_symbol(symbol)
                            if latest:
                                await websocket.send(json.dumps(latest))
                    
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from {client_addr}: {message}")
                except Exception as e:
                    logger.error(f"Error handling message from {client_addr}: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Error in websocket_handler for {client_addr}: {e}")
        finally:
            # Remove subscriber on disconnect
            self.subscribers.discard(websocket)
            self._client_info.pop(websocket, None)
            logger.info(f"Client disconnected: {client_addr} (Total: {len(self.subscribers)})")
    
    def get_latest_for_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest record for a specific symbol."""
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                return None
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                       delta, gamma, theta, vega, iv, source
                FROM ltp_ticks 
                WHERE symbol = %s 
                ORDER BY ts DESC 
                LIMIT 1
            """, (symbol,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._row_to_record(cursor, row)
            return None
        except Exception as e:
            logger.error(f"Error getting latest for {symbol}: {e}")
            return None
    
    def set_mode(self, mode: str):
        """Switch between LIVE and REPLAY modes (thread-safe)."""
        with self.mode_lock:
            if mode not in ["LIVE", "REPLAY"]:
                logger.error(f"Invalid mode: {mode}. Must be 'LIVE' or 'REPLAY'")
                return
            
            if self.mode != mode:
                logger.info(f"Switching mode from {self.mode} to {mode}")
                self.mode = mode
                
                # Reset replay state when switching to LIVE
                if mode == "LIVE":
                    with self.replay_config_lock:
                        # Clear replay records to free memory
                        self.replay_config['replay_records'] = None
                        self.replay_config['replay_index'] = 0
                        self.replay_config['current_position'] = 0
                        self.replay_config['current_timestamp'] = None
                        self.replay_config['paused'] = False
                        self.replay_config['replay_event'].set()
                elif mode == "REPLAY":
                    # When switching to REPLAY, don't load records yet
                    # Records will be loaded when Start is clicked
                    with self.replay_config_lock:
                        # Only clear if we want to force reload
                        # Keep existing records if they match the config
                        pass
    
    def get_mode(self) -> str:
        """Get current mode."""
        with self.mode_lock:
            return self.mode
    
    def set_replay_config(self, start_time: str, end_time: str, speed: float = 1.0):
        """Configure replay parameters (thread-safe)."""
        with self.replay_config_lock:
            self.replay_config['start_time'] = start_time
            self.replay_config['end_time'] = end_time
            self.replay_config['speed'] = max(0.1, min(100.0, speed))  # Clamp between 0.1x and 100x
            self.replay_config['replay_index'] = 0
            self.replay_config['current_position'] = 0
            self.replay_config['current_timestamp'] = None
            # Don't clear replay_records here - only clear when mode changes or explicitly reset
            logger.info(f"Replay config set: {start_time} to {end_time} at {speed}x speed")
    
    def load_replay_records(self) -> bool:
        """Load replay records from database (call this when Start is clicked). Returns True if successful."""
        logger.info("Loading replay records from database...")
        
        with self.replay_config_lock:
            start_time = self.replay_config.get('start_time')
            end_time = self.replay_config.get('end_time')
        
        if not start_time or not end_time:
            logger.error("Replay start_time and end_time must be set before loading records")
            return False
        
        # Load records (this may take time for large date ranges)
        records = self._load_replay_records()
        
        if not records:
            logger.error("No records found for replay. Check date range.")
            return False
        
        # Store loaded records
        with self.replay_config_lock:
            self.replay_config['replay_records'] = records
            self.replay_config['total_records'] = len(records)
            self.replay_config['replay_index'] = 0
            self.replay_config['current_position'] = 0
            self.replay_config['current_timestamp'] = None
        
        logger.info(f"Successfully loaded {len(records)} records for replay")
        return True
    
    def pause_replay(self):
        """Pause replay (thread-safe)."""
        with self.replay_config_lock:
            self.replay_config['paused'] = True
            self.replay_config['replay_event'].clear()
            logger.info("Replay paused")
    
    def resume_replay(self):
        """Resume replay (thread-safe)."""
        with self.replay_config_lock:
            self.replay_config['paused'] = False
            self.replay_config['replay_event'].set()
            logger.info("Replay resumed")
    
    def stop_replay(self):
        """Stop and reset replay (thread-safe)."""
        with self.replay_config_lock:
            self.replay_config['replay_index'] = 0
            self.replay_config['current_position'] = 0
            self.replay_config['current_timestamp'] = None
            self.replay_config['paused'] = False
            self.replay_config['replay_event'].set()
            logger.info("Replay stopped and reset")
    
    def get_replay_progress(self) -> Dict[str, Any]:
        """Get current replay progress (thread-safe)."""
        with self.replay_config_lock:
            total = self.replay_config['total_records']
            current = self.replay_config['current_position']
            progress_pct = (current / total * 100) if total > 0 else 0.0
            
            return {
                'current': current,
                'total': total,
                'progress_pct': progress_pct,
                'paused': self.replay_config['paused'],
                'speed': self.replay_config['speed'],
                'start_time': self.replay_config['start_time'],
                'end_time': self.replay_config['end_time'],
                'current_timestamp': self.replay_config['current_timestamp']  # Current timestamp being broadcast
            }
    
    def set_progress_callback(self, callback):
        """Set callback function for progress updates (called from replay loop)."""
        self.progress_callback = callback
    
    def _load_replay_records(self) -> list:
        """Load replay records from PostgreSQL for configured time range (non-blocking)."""
        with self.replay_config_lock:
            start_time = self.replay_config['start_time']
            end_time = self.replay_config['end_time']
        
        if not start_time or not end_time:
            logger.error("Replay start_time and end_time must be set")
            return []
        
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                return []
            cursor = conn.cursor()
            
            check_query = "SELECT COUNT(*) FROM ltp_ticks WHERE ts = %s::timestamptz"
            cursor.execute(check_query, (start_time,))
            exact_match_count = cursor.fetchone()[0]
            
            if exact_match_count == 0:
                cursor.execute("SELECT ts FROM ltp_ticks WHERE ts >= %s::timestamptz ORDER BY ts LIMIT 1", (start_time,))
                first_result = cursor.fetchone()
                if first_result:
                    first_available_ts = first_result[0]
                    logger.warning(f"No records found at exact start_time {start_time}")
                    logger.warning(f"First available record is at {first_available_ts}")
            
            query = """
                SELECT symbol, token, ts, ltp, bid, ask, volume, oi,
                       delta, gamma, theta, vega, iv, source
                FROM ltp_ticks 
                WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
                ORDER BY ts
            """
            cursor.execute(query, (start_time, end_time))
            
            records = []
            batch_size = 10000
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    record = self._row_to_record(cursor, row)
                    record['source'] = 'replay'
                    records.append(record)
                if self.get_mode() != "REPLAY":
                    conn.close()
                    return []
            
            conn.close()
            if records:
                first_ts = records[0]['ts']
                last_ts = records[-1]['ts']
                logger.info(f"Loaded {len(records)} records for replay ({start_time} to {end_time})")
                logger.info(f"First record timestamp: {first_ts}, Last: {last_ts}")
                if first_ts != start_time:
                    logger.warning(f"First record ({first_ts}) does not match start_time ({start_time})")
            return records
        except Exception as e:
            logger.error(f"Error loading replay records: {e}")
            return []
    
    async def replay_mode(self):
        """Replay historical data from database at configurable speed."""
        # Check if records are loaded before starting
        with self.replay_config_lock:
            if self.replay_config['replay_records'] is None:
                # Records not loaded yet - wait a bit and check again
                # This allows time for the Start button to trigger loading
                await asyncio.sleep(1.0)
                
                # Check again
                if self.replay_config['replay_records'] is None:
                    # Still not loaded - exit gracefully (don't log warning repeatedly)
                    return
            
            records = self.replay_config['replay_records']
            replay_index = self.replay_config['replay_index']
            speed = self.replay_config['speed']
        
        if not records:
            return
        
        logger.info(f"Starting replay mode with {len(records)} records...")
        
        # Group records by timestamp
        current_ts = None
        batch = []
        last_broadcast_ts = None
        
        # Start from current replay_index
        for idx in range(replay_index, len(records)):
            # Check if we should stop
            if not self.running:
                logger.info("Replay stopped (service stopping)")
                break
            
            # Check if mode changed - break immediately if it did
            current_mode = self.get_mode()
            if current_mode != "REPLAY":
                logger.info(f"Mode changed to {current_mode}, exiting replay mode")
                break
            
            # Check if paused
            self.replay_config['replay_event'].wait()
            
            record = records[idx]
            ts = record['ts']
            
            # Group records by timestamp
            if current_ts is None:
                current_ts = ts
            
            if ts == current_ts:
                batch.append(record)
            else:
                # Broadcast batch for previous timestamp
                if batch:
                    await self.broadcast_records(batch)
                    
                    # Update progress and current timestamp
                    with self.replay_config_lock:
                        self.replay_config['current_position'] = idx
                        self.replay_config['replay_index'] = idx
                        self.replay_config['current_timestamp'] = current_ts  # Store current timestamp being broadcast
                    
                    # Call progress callback if set
                    if self.progress_callback:
                        try:
                            self.progress_callback(self.get_replay_progress())
                        except Exception as e:
                            logger.error(f"Error in progress callback: {e}")
                    
                    # Calculate sleep time - always use 5 seconds between timestamp groups (adjusted for speed)
                    # This ensures realistic timing: broadcast all records for one timestamp, wait 5 seconds, then next timestamp
                    # Minimum sleep time is 0.1 seconds to prevent too-fast broadcasting
                    sleep_time = max(0.1, 5.0 / speed)  # 5 seconds divided by speed multiplier, minimum 0.1s
                    
                    logger.debug(f"Broadcasted {len(batch)} records for timestamp {current_ts}, waiting {sleep_time:.2f}s before next batch")
                    
                    # Sleep in small increments to check mode more frequently
                    sleep_remaining = sleep_time
                    while sleep_remaining > 0:
                        if self.get_mode() != "REPLAY" or not self.broadcasting_active:
                            break
                        sleep_chunk = min(0.5, sleep_remaining)  # Check every 0.5 seconds
                        await asyncio.sleep(sleep_chunk)
                        sleep_remaining -= sleep_chunk
                    
                    # Check mode again after sleep
                    if self.get_mode() != "REPLAY" or not self.broadcasting_active:
                        break
                
                # Start new batch
                batch = [record]
                current_ts = ts
                last_broadcast_ts = current_ts if last_broadcast_ts else current_ts
            
            # Small delay to prevent tight loop
            await asyncio.sleep(0.001)
        
        # Broadcast final batch
        if batch and self.running and self.get_mode() == "REPLAY":
            await self.broadcast_records(batch)
            
            with self.replay_config_lock:
                self.replay_config['current_position'] = len(records)
                self.replay_config['replay_index'] = len(records)
                if current_ts:
                    self.replay_config['current_timestamp'] = current_ts  # Store final timestamp
        
        logger.info("Replay completed or mode changed")
    
    async def live_mode(self):
        """Live mode: Monitor database for new records (existing functionality)."""
        logger.info("Starting live mode (database monitoring)...")
        
        while self.running:
            try:
                # Check if mode changed - break immediately if it did
                current_mode = self.get_mode()
                if current_mode != "LIVE":
                    logger.info(f"Mode changed to {current_mode}, exiting live mode")
                    break
                
                # Get new records from database
                new_records = self.get_new_records()
                
                # Broadcast to all clients
                if new_records:
                    await self.broadcast_records(new_records)
                
                # Wait before next check (check mode periodically during wait)
                elapsed = 0
                while elapsed < MONITOR_INTERVAL and self.running:
                    if self.get_mode() != "LIVE":
                        break
                    await asyncio.sleep(0.5)  # Check every 0.5 seconds
                    elapsed += 0.5
            
            except Exception as e:
                logger.error(f"Error in live_mode loop: {e}")
                await asyncio.sleep(1)
    
    async def monitor_and_broadcast(self):
        """
        Main loop: Route to live_mode or replay_mode based on current mode.
        Waits for user to start broadcasting (unless auto_start is True).
        """
        logger.info("Broadcast loop ready (waiting for Start command)...")
        
        # Wait for broadcasting to be started (unless auto_start)
        if not self.auto_start:
            while self.running and not self.broadcasting_active:
                await asyncio.sleep(0.5)
        
        logger.info("Starting broadcast loop...")
        
        while self.running:
            try:
                # Check if broadcasting is active (user must click Start)
                if not self.broadcasting_active:
                    await asyncio.sleep(1)
                    continue
                
                current_mode = self.get_mode()
                
                if current_mode == "LIVE":
                    await self.live_mode()
                elif current_mode == "REPLAY":
                    # Check if records are loaded before attempting replay
                    with self.replay_config_lock:
                        has_records = self.replay_config['replay_records'] is not None
                    
                    if has_records:
                        await self.replay_mode()
                        # After replay completes, check if we should continue
                        if self.running and self.get_mode() == "REPLAY" and self.broadcasting_active:
                            # Replay completed, wait a bit before checking again
                            await asyncio.sleep(1)
                    else:
                        # Records not loaded yet - wait and check mode again
                        # This prevents the loop from spinning when in REPLAY mode without records
                        await asyncio.sleep(2)
                else:
                    logger.error(f"Unknown mode: {current_mode}")
                    await asyncio.sleep(1)
            
            except Exception as e:
                logger.error(f"Error in monitor_and_broadcast loop: {e}")
                await asyncio.sleep(1)
    
    async def start_websocket_server(self):
        """Start WebSocket server for client connections."""
        logger.info(f"Starting WebSocket server on {self.ws_host}:{self.ws_port}")
        
        # websockets 15.0+ handler receives ServerConnection (not websocket, path)
        async def handler(connection):
            # Call our handler with the connection
            await self.websocket_handler(connection)
        
        try:
            # Use serve with the handler - websockets 15.0+ requires await
            async with websockets.serve(
                handler,
                self.ws_host,
                self.ws_port,
                ping_interval=20,
                ping_timeout=20
            ):
                logger.info(f"[OK] WebSocket server running on ws://{self.ws_host}:{self.ws_port}")
                logger.info("Waiting for client connections...")
                
                # Keep server running
                await asyncio.Future()  # Run forever
        except Exception as e:
            logger.error(f"Error starting WebSocket server: {e}", exc_info=True)
            raise
    
    async def start(self):
        """Start the broadcaster service."""
        self.running = True
        
        logger.info("=" * 70)
        logger.info("WEBSOCKET BROADCASTER SERVICE")
        logger.info("=" * 70)
        logger.info("Database: PostgreSQL (DATABASE_URL)")
        logger.info(f"WebSocket: ws://{self.ws_host}:{self.ws_port}")
        logger.info(f"Monitor Interval: {MONITOR_INTERVAL} seconds")
        logger.info("=" * 70)
        
        # Start WebSocket server and monitoring loop concurrently
        await asyncio.gather(
            self.start_websocket_server(),
            self.monitor_and_broadcast()
        )
    
    def start_broadcasting(self):
        """Start broadcasting (called when user clicks Start button)."""
        with self.broadcasting_lock:
            if not self.broadcasting_active:
                self.broadcasting_active = True
                logger.info("Broadcasting started by user")
    
    def stop_broadcasting(self):
        """Stop broadcasting (called when user clicks Stop button)."""
        with self.broadcasting_lock:
            if self.broadcasting_active:
                self.broadcasting_active = False
                logger.info("Broadcasting stopped by user")
                # Also stop replay if active
                if self.get_mode() == "REPLAY":
                    self.stop_replay()
    
    def stop(self):
        """Stop the broadcaster service."""
        logger.info("Stopping WebSocket Broadcaster Service...")
        self.running = False
        self.broadcasting_active = False


async def main():
    """Main entry point. Uses DATABASE_URL for PostgreSQL."""
    try:
        db.get_connection().close()
    except Exception as e:
        logger.warning("Cannot connect to database: %s", e)
        logger.warning("Set DATABASE_URL (e.g. postgresql://nifty_app:nifty_app_pw@localhost:5432/Centralized_Index_Option_Data)")
    
    broadcaster = WebSocketBroadcaster(
        db_path=None,
        ws_host=WS_HOST,
        ws_port=WS_PORT
    )
    
    try:
        # Start the service
        await broadcaster.start()
    except KeyboardInterrupt:
        logger.info("Service stopped by user")
        broadcaster.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        broadcaster.stop()
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service terminated")

