"""
Real-Time Data Client Library (PostgreSQL)
For local projects to access real-time data from local data centre
"""

import sys
import websocket
import json
import threading
from typing import Callable, Optional, List, Dict, Any
from pathlib import Path
import logging

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

DEFAULT_WS_URL = "ws://localhost:8765"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RealtimeDataClient:
    """Client for accessing real-time data from local data centre (PostgreSQL)"""
    
    def __init__(
        self, 
        ws_url: str = DEFAULT_WS_URL,
        db_path: Optional[Path] = None,
        on_data_callback: Optional[Callable] = None
    ):
        """
        Initialize client. Database connection uses DATABASE_URL (db_path ignored for PostgreSQL).
        """
        self.ws_url = ws_url
        self.db_path = db_path  # Kept for API compat; not used
        self.on_data_callback = on_data_callback
        
        self.ws = None
        self.ws_thread = None
        self.running = False
        self.db_conn = None
        
        logger.info(f"RealtimeDataClient initialized (WS: {ws_url})")
    
    def connect(self):
        """Connect to local data service WebSocket"""
        if self.running:
            logger.warning("Already connected")
            return
        
        self.running = True
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                if self.on_data_callback:
                    self.on_data_callback(data)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding message: {e}")
        
        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
            self.running = False
        
        def on_open(ws):
            logger.info("WebSocket connected")
        
        # Create WebSocket connection
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        # Run WebSocket in separate thread
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        
        logger.info("WebSocket connection started")
    
    def disconnect(self):
        """Disconnect from WebSocket"""
        if self.ws:
            self.running = False
            self.ws.close()
            logger.info("WebSocket disconnected")
    
    def _get_db_connection(self):
        """Get PostgreSQL connection (lazy initialization). Uses DATABASE_URL."""
        if not self.db_conn:
            self.db_conn = db.get_connection()
        return self.db_conn
    
    @staticmethod
    def _row_to_dict(cursor, row) -> dict:
        if row is None:
            return {}
        return {cursor.description[i][0]: row[i] for i in range(len(row))}
    
    def get_latest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest tick for a symbol."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM ltp_ticks WHERE symbol = %s ORDER BY ts DESC LIMIT 1",
            (symbol,)
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_dict(cursor, row)
        return None
    
    def get_latest_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get latest ticks for all symbols."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ltp_ticks ORDER BY ts DESC LIMIT %s", (limit,))
        return [self._row_to_dict(cursor, row) for row in cursor.fetchall()]
    
    def get_historical(
        self, 
        symbol: str, 
        start_ts: str, 
        end_ts: str
    ) -> List[Dict[str, Any]]:
        """Get historical data for a symbol."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM ltp_ticks 
            WHERE symbol = %s AND ts >= %s::timestamptz AND ts <= %s::timestamptz
            ORDER BY ts
            """,
            (symbol, start_ts, end_ts)
        )
        return [self._row_to_dict(cursor, row) for row in cursor.fetchall()]
    
    def get_symbols(self) -> List[str]:
        """Get list of all symbols in database."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM ltp_ticks ORDER BY symbol")
        return [row[0] for row in cursor.fetchall()]
    
    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute custom SQL query. Use %s placeholders for params."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [self._row_to_dict(cursor, row) for row in cursor.fetchall()]
    
    def close(self):
        """Close all connections"""
        self.disconnect()
        
        if self.db_conn:
            self.db_conn.close()
            self.db_conn = None
        
        logger.info("Client closed")


# Example usage
if __name__ == "__main__":
    def on_data(data):
        """Callback for real-time data"""
        print(f"Received: {data['symbol']} @ {data['ltp']}")
    
    # Create client
    client = RealtimeDataClient(on_data_callback=on_data)
    
    # Connect to real-time feed
    client.connect()
    
    # Query historical data
    import time
    time.sleep(2)  # Wait for some data
    
    latest = client.get_latest("NIFTY 50")
    print(f"Latest NIFTY 50: {latest}")
    
    symbols = client.get_symbols()
    print(f"Available symbols: {symbols}")
    
    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.close()
