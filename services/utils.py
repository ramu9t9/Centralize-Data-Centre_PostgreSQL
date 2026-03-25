#!/usr/bin/env python3
"""
Utility functions for timezone conversion
"""

from datetime import datetime, timezone, timedelta

def utc_to_ist(utc_timestamp: str) -> str:
    """Convert UTC timestamp string to IST (Indian Standard Time) string"""
    try:
        # Parse UTC timestamp
        if 'Z' in utc_timestamp:
            utc_timestamp = utc_timestamp.replace('Z', '+00:00')
        
        dt = datetime.fromisoformat(utc_timestamp)
        
        # If no timezone info, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Convert to IST (UTC+5:30)
        ist_offset = timedelta(hours=5, minutes=30)
        ist_dt = dt + ist_offset
        
        # Format as YYYY-MM-DD HH:MM:SS IST
        return ist_dt.strftime('%Y-%m-%d %H:%M:%S IST')
    except Exception:
        # If conversion fails, return original with IST label
        return f"{utc_timestamp[:19]} IST"

def format_timestamp_for_display(timestamp: str) -> str:
    """Format timestamp for display (convert UTC to IST)"""
    if not timestamp:
        return "N/A"
    
    try:
        # Extract just the date-time part (first 19 characters: YYYY-MM-DDTHH:MM:SS)
        dt_part = timestamp[:19]
        return utc_to_ist(timestamp)
    except:
        return timestamp[:19] if len(timestamp) >= 19 else timestamp

