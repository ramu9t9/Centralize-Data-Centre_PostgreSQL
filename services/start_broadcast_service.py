#!/usr/bin/env python3
"""
Start Broadcast Service with GUI Control Panel
Main entry point that creates broadcaster, GUI, and links them together
"""

import asyncio
import threading
import sys
from pathlib import Path
from tkinter import messagebox

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.websocket_broadcaster_service import WebSocketBroadcaster
from services.broadcast_control_panel import BroadcastControlPanel
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_broadcaster(broadcaster: WebSocketBroadcaster):
    """Run broadcaster in asyncio event loop (in background thread)"""
    try:
        asyncio.run(broadcaster.start())
    except Exception as e:
        logger.error(f"Error in broadcaster: {e}", exc_info=True)


def main():
    """Main entry point"""
    logger.info("=" * 70)
    logger.info("STARTING BROADCAST SERVICE WITH GUI")
    logger.info("=" * 70)
    
    # Database path
    db_path = Path(__file__).parent.parent / "data" / "nifty_local.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not db_path.exists():
        logger.warning(f"Database not found: {db_path}")
        logger.warning("Database will be created when VPS collector starts")
    
    # Create broadcaster instance (auto_start=False means it won't broadcast until user clicks Start)
    broadcaster = WebSocketBroadcaster(
        db_path=db_path,
        ws_host="localhost",
        ws_port=8765,
        auto_start=False  # Don't start broadcasting automatically
    )
    
    # Create GUI panel
    panel = BroadcastControlPanel(broadcaster=broadcaster)
    
    # Set progress callback for GUI updates
    def progress_callback(progress):
        """Callback for replay progress updates"""
        try:
            panel.message_queue.put(f"Replay progress: {progress['progress_pct']:.1f}% ({progress['current']:,}/{progress['total']:,})")
        except Exception as e:
            logger.error(f"Error in progress callback: {e}")
    
    broadcaster.set_progress_callback(progress_callback)
    
    # Start broadcaster in background thread
    broadcaster_thread = threading.Thread(
        target=run_broadcaster,
        args=(broadcaster,),
        daemon=True,
        name="BroadcasterThread"
    )
    broadcaster_thread.start()
    
    logger.info("Broadcaster started in background thread")
    logger.info("Starting GUI...")
    
    # Run GUI (blocks until window is closed)
    try:
        # Make sure GUI window is visible
        panel.root.update()
        panel.root.deiconify()  # Ensure window is visible
        
        logger.info("GUI window should now be visible")
        panel.run()
    except KeyboardInterrupt:
        logger.info("GUI closed by user")
    except Exception as e:
        logger.error(f"Error running GUI: {e}", exc_info=True)
        try:
            messagebox.showerror("Error", f"GUI Error: {e}")
        except:
            print(f"GUI Error: {e}")
    finally:
        # Stop broadcaster
        logger.info("Stopping broadcaster...")
        broadcaster.stop()
        
        # Wait for thread to finish (with timeout)
        broadcaster_thread.join(timeout=5.0)
        
        logger.info("Service stopped")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Service terminated by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

