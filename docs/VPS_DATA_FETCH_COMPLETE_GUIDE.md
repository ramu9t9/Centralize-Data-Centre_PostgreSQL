# 📥 Complete Guide: Fetching NIFTY Data from VPS for Other Projects

## 📋 **Overview**

This comprehensive guide provides all credentials, methods, and code examples to fetch NIFTY options data from your Hostinger VPS server for use in other projects.

**Last Updated:** November 26, 2025  
**Database Location:** `/opt/nifty-data-collector/nifty_local.db`  
**VPS IP:** `31.97.233.93`

---

## 🔑 **Section 1: All Credentials**

### **1.1 VPS SSH Access**

```bash
# SSH Connection Details
Host: 31.97.233.93
Username: root
Port: 22 (default)

# SSH Key Path (Windows)
SSH Key: $env:USERPROFILE\.ssh\nifty_server_key

# SSH Command
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93

# Alternative: Password-based (if key not available)
Password: Angelone#8169752036
```

### **1.2 Hostinger API Credentials**

```python
# Hostinger API Token
API_TOKEN = "E6Rm6hRne4WODcnDkegLcaedLCqYBRpTOZbNd04Z0afecf62"

# API Base URL
API_BASE_URL = "https://developers.hostinger.com/api/vps/v1"

# Usage Example
headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}
```

### **1.3 Database Credentials**

```python
# Database Path on VPS
DB_PATH_VPS = "/opt/nifty-data-collector/nifty_local.db"

# Database Type
DB_TYPE = "SQLite"

# Database User (SQLite doesn't require user/password)
# Access is file-system based via SSH
```

### **1.4 Telegram Bot Credentials**

```python
# Telegram Bot Token
BOT_TOKEN = "8374731275:AAFIM2DcsZgVja6cgic6jKOux2zZ8hguJPo"

# Allowed User ID
ALLOWED_USER_ID = 1022980118

# Bot Commands Available
# /status, /stats, /logs, /latest_records, /start_service, /stop_service
```

### **1.5 Angel One API Credentials (for reference)**

```python
# Angel One API (used by data collection service)
ANGEL_API_KEY = "IF0vWmnY"
ANGEL_USER_ID = "r117172"
ANGEL_PIN = 9029
ANGEL_TOTP_SECRET = "Y4GDOA6SL5VOCKQPFLR5EM3HOY"
```

---

## 📊 **Section 2: Database Structure**

### **2.1 Table Schema**

```sql
-- Main table: ltp_ticks
CREATE TABLE ltp_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,           -- Trading symbol (e.g., 'NIFTY 50', 'NIFTY25NOV2526000CE')
    token TEXT NOT NULL,             -- Angel One token ID
    ts TEXT NOT NULL,                -- Timestamp (ISO format, UTC)
    ltp REAL,                        -- Last Traded Price
    bid REAL,                        -- Best Bid Price
    ask REAL,                        -- Best Ask Price
    volume INTEGER,                  -- Trading Volume
    oi INTEGER,                      -- Open Interest
    delta REAL,                      -- Option Delta (Greeks)
    gamma REAL,                      -- Option Gamma (Greeks)
    theta REAL,                      -- Option Theta (Greeks)
    vega REAL,                       -- Option Vega (Greeks)
    iv REAL,                         -- Implied Volatility
    source TEXT DEFAULT 'ws'         -- Data source ('ws' or 'api')
);

-- Indexes for performance
CREATE INDEX idx_ticks_symbol_ts ON ltp_ticks(symbol, ts);
CREATE INDEX idx_ticks_ts ON ltp_ticks(ts);
CREATE INDEX idx_ticks_symbol ON ltp_ticks(symbol);
CREATE UNIQUE INDEX idx_ticks_symbol_ts_unique ON ltp_ticks(symbol, ts);
```

### **2.2 Symbol Types**

```python
# Symbol Patterns:
# 1. Index: "NIFTY 50"
# 2. Options: "NIFTY{EXPIRY}{STRIKE}{TYPE}"
#    Example: "NIFTY25NOV2526000CE" (Call) or "NIFTY25NOV2526000PE" (Put)
# 3. Futures: "NIFTY{EXPIRY}FUT"
#    Example: "NIFTY28NOV25FUT"
```

### **2.3 Data Statistics (as of Nov 2025)**

```
Total Records: ~6.8 million
Date Range: August 14, 2025 - November 25, 2025
Unique Symbols: ~538 option strikes
Data Collection: Every 5 seconds during market hours (09:15-15:30 IST)
Complete Data Available: From August 19, 2025 onwards
```

---

## 🚀 **Section 3: Methods to Fetch Data**

### **Method 1: Direct SSH + SQLite (Recommended for Large Exports)**

#### **3.1.1 Download Entire Database**

```bash
# PowerShell (Windows)
scp -i $env:USERPROFILE\.ssh\nifty_server_key `
    root@31.97.233.93:/opt/nifty-data-collector/nifty_local.db `
    "G:\Projects\YourProject\data\nifty_local.db"

# Linux/Mac
scp -i ~/.ssh/nifty_server_key \
    root@31.97.233.93:/opt/nifty-data-collector/nifty_local.db \
    ~/projects/your_project/data/nifty_local.db
```

#### **3.1.2 Export Specific Data via SSH**

```bash
# Export last 24 hours to CSV
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "sqlite3 /opt/nifty-data-collector/nifty_local.db \
    -header -csv \
    \"SELECT * FROM ltp_ticks WHERE ts > datetime('now', '-24 hours') ORDER BY ts DESC;\" \
    > /tmp/nifty_24h.csv"

# Download the CSV
scp -i $env:USERPROFILE\.ssh\nifty_server_key \
    root@31.97.233.93:/tmp/nifty_24h.csv \
    "G:\Projects\YourProject\data\nifty_24h.csv"
```

#### **3.1.3 Export Options Data Only**

```bash
# Export options with Greeks
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "sqlite3 /opt/nifty-data-collector/nifty_local.db \
    -header -csv \
    \"SELECT symbol, ts, ltp, oi, delta, gamma, theta, vega, iv \
    FROM ltp_ticks \
    WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%') \
    AND oi IS NOT NULL AND oi > 0 \
    AND delta IS NOT NULL \
    ORDER BY ts DESC;\" \
    > /tmp/nifty_options.csv"

# Download
scp -i $env:USERPROFILE\.ssh\nifty_server_key \
    root@31.97.233.93:/tmp/nifty_options.csv \
    "G:\Projects\YourProject\data\nifty_options.csv"
```

---

### **Method 2: Python Script with SSH Connection**

#### **3.2.1 Complete Python Client**

```python
#!/usr/bin/env python3
"""
NIFTY Data Fetcher - Complete Client
Fetches data from VPS using SSH and SQLite
"""

import sqlite3
import pandas as pd
import paramiko
import io
from datetime import datetime, timedelta
import pytz
from pathlib import Path

class NiftyDataFetcher:
    """Complete client for fetching NIFTY data from VPS"""
    
    def __init__(self, 
                 vps_host="31.97.233.93",
                 vps_user="root",
                 ssh_key_path=None,
                 vps_password=None,
                 db_path="/opt/nifty-data-collector/nifty_local.db"):
        """
        Initialize fetcher
        
        Args:
            vps_host: VPS IP address
            vps_user: SSH username
            ssh_key_path: Path to SSH private key (Windows: $env:USERPROFILE\.ssh\nifty_server_key)
            vps_password: SSH password (if key not available)
            db_path: Database path on VPS
        """
        self.vps_host = vps_host
        self.vps_user = vps_user
        self.ssh_key_path = ssh_key_path
        self.vps_password = vps_password
        self.db_path = db_path
        self.ssh_client = None
        
    def connect(self):
        """Establish SSH connection"""
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            if self.ssh_key_path:
                # Use SSH key
                key = paramiko.RSAKey.from_private_key_file(self.ssh_key_path)
                self.ssh_client.connect(
                    hostname=self.vps_host,
                    username=self.vps_user,
                    pkey=key,
                    timeout=10
                )
            else:
                # Use password
                self.ssh_client.connect(
                    hostname=self.vps_host,
                    username=self.vps_user,
                    password=self.vps_password,
                    timeout=10
                )
            print("✅ SSH connection established")
            return True
        except Exception as e:
            print(f"❌ SSH connection failed: {e}")
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        if self.ssh_client:
            self.ssh_client.close()
            print("✅ SSH connection closed")
    
    def execute_sql(self, query, params=None):
        """Execute SQL query on remote database"""
        if not self.ssh_client:
            raise Exception("SSH not connected. Call connect() first.")
        
        # Build SQLite command
        if params:
            # Convert params to SQLite format
            param_str = " ".join([f"'{p}'" for p in params])
            cmd = f"sqlite3 {self.db_path} \"{query}\""
        else:
            cmd = f"sqlite3 {self.db_path} \"{query}\""
        
        stdin, stdout, stderr = self.ssh_client.exec_command(cmd)
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        
        if error:
            print(f"⚠️ SQL Error: {error}")
        
        return output
    
    def fetch_dataframe(self, query, params=None):
        """Fetch data as pandas DataFrame"""
        if not self.ssh_client:
            raise Exception("SSH not connected. Call connect() first.")
        
        # Export to CSV via SSH
        csv_query = f".mode csv\n.headers on\n{query}"
        
        stdin, stdout, stderr = self.ssh_client.exec_command(
            f"sqlite3 {self.db_path} << 'EOF'\n{csv_query}\nEOF"
        )
        
        csv_data = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        
        if error:
            print(f"⚠️ Error: {error}")
            return pd.DataFrame()
        
        # Convert CSV string to DataFrame
        from io import StringIO
        df = pd.read_csv(StringIO(csv_data))
        
        # Convert timestamp
        if 'ts' in df.columns:
            df['ts'] = pd.to_datetime(df['ts'])
        
        return df
    
    def get_latest_data(self, limit=100, symbol=None):
        """Get latest data records"""
        if symbol:
            query = f"""
            SELECT * FROM ltp_ticks 
            WHERE symbol = '{symbol}'
            ORDER BY ts DESC 
            LIMIT {limit}
            """
        else:
            query = f"""
            SELECT * FROM ltp_ticks 
            ORDER BY ts DESC 
            LIMIT {limit}
            """
        return self.fetch_dataframe(query)
    
    def get_data_by_date_range(self, start_date, end_date, symbol=None):
        """Get data for date range"""
        if symbol:
            query = f"""
            SELECT * FROM ltp_ticks 
            WHERE symbol = '{symbol}'
            AND ts >= '{start_date}' AND ts <= '{end_date}'
            ORDER BY ts
            """
        else:
            query = f"""
            SELECT * FROM ltp_ticks 
            WHERE ts >= '{start_date}' AND ts <= '{end_date}'
            ORDER BY ts
            """
        return self.fetch_dataframe(query)
    
    def get_options_data(self, expiry_date=None, strike_range=None, hours=24):
        """Get options data with filters"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        query = f"""
        SELECT * FROM ltp_ticks 
        WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
        AND symbol NOT LIKE '%FUT%'
        AND oi IS NOT NULL AND oi > 0
        AND delta IS NOT NULL
        AND ts >= '{cutoff}'
        """
        
        if expiry_date:
            query += f" AND symbol LIKE '%{expiry_date}%'"
        
        query += " ORDER BY ts DESC"
        
        return self.fetch_dataframe(query)
    
    def download_database(self, local_path):
        """Download entire database file"""
        if not self.ssh_client:
            raise Exception("SSH not connected. Call connect() first.")
        
        sftp = self.ssh_client.open_sftp()
        sftp.get(self.db_path, local_path)
        sftp.close()
        print(f"✅ Database downloaded to {local_path}")
        return local_path

# Usage Example
if __name__ == "__main__":
    # Initialize fetcher
    fetcher = NiftyDataFetcher(
        ssh_key_path=r"C:\Users\Ram\.ssh\nifty_server_key"  # Adjust path
    )
    
    # Connect
    if fetcher.connect():
        try:
            # Get latest 100 records
            df_latest = fetcher.get_latest_data(limit=100)
            print(f"📊 Latest data: {len(df_latest)} records")
            print(df_latest.head())
            
            # Get NIFTY 50 data
            df_nifty = fetcher.get_latest_data(limit=1000, symbol="NIFTY 50")
            print(f"\n📈 NIFTY 50 data: {len(df_nifty)} records")
            
            # Get options data
            df_options = fetcher.get_options_data(hours=24)
            print(f"\n🎯 Options data: {len(df_options)} records")
            
            # Download entire database
            # fetcher.download_database("G:\\Projects\\YourProject\\data\\nifty_local.db")
            
        finally:
            fetcher.disconnect()
```

#### **3.2.2 Installation Requirements**

```bash
# Install required Python packages
pip install paramiko pandas sqlalchemy
```

---

### **Method 3: Using Hostinger API (For VPS Management)**

```python
#!/usr/bin/env python3
"""
Hostinger API Client - For VPS management and metadata
Note: This doesn't directly access database, but helps manage VPS
"""

import requests

class HostingerAPIClient:
    """Client for Hostinger API"""
    
    def __init__(self, api_token="E6Rm6hRne4WODcnDkegLcaedLCqYBRpTOZbNd04Z0afecf62"):
        self.api_token = api_token
        self.base_url = "https://developers.hostinger.com/api/vps/v1"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
    
    def get_vps_list(self):
        """Get list of VPS instances"""
        response = requests.get(
            f"{self.base_url}/virtual-machines",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def get_vps_details(self, vps_id):
        """Get VPS details"""
        response = requests.get(
            f"{self.base_url}/virtual-machines/{vps_id}",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

# Usage
client = HostingerAPIClient()
vps_list = client.get_vps_list()
print(f"Found {len(vps_list.get('data', []))} VPS instances")
```

---

## 📦 **Section 4: Integration Examples for Other Projects**

### **4.1 Python Project Integration**

```python
# your_project/data_fetcher.py
from nifty_data_fetcher import NiftyDataFetcher
import pandas as pd

# Initialize
fetcher = NiftyDataFetcher(
    ssh_key_path=r"C:\Users\Ram\.ssh\nifty_server_key"
)

# Connect and fetch
fetcher.connect()
data = fetcher.get_options_data(hours=24)
fetcher.disconnect()

# Use in your analysis
print(f"Fetched {len(data)} option records")
# Your analysis code here...
```

### **4.2 Jupyter Notebook Integration**

```python
# In your Jupyter notebook
%load_ext autoreload
%autoreload 2

from nifty_data_fetcher import NiftyDataFetcher

# Setup
fetcher = NiftyDataFetcher(ssh_key_path="~/.ssh/nifty_server_key")
fetcher.connect()

# Fetch data
df = fetcher.get_latest_data(limit=10000)

# Analyze
import matplotlib.pyplot as plt
df['ltp'].plot()
plt.show()

fetcher.disconnect()
```

### **4.3 Export to Different Formats**

```python
# Export to CSV
df.to_csv("nifty_data.csv", index=False)

# Export to JSON
df.to_json("nifty_data.json", orient='records', indent=2)

# Export to Excel
df.to_excel("nifty_data.xlsx", index=False)

# Export to Parquet (efficient for large datasets)
df.to_parquet("nifty_data.parquet", compression='snappy')
```

---

## 🔧 **Section 5: Quick Reference Commands**

### **5.1 SSH Commands**

```bash
# Connect to VPS
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93

# Check database size
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "ls -lh /opt/nifty-data-collector/nifty_local.db"

# Check recent records
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "sqlite3 /opt/nifty-data-collector/nifty_local.db \
    'SELECT COUNT(*) FROM ltp_ticks WHERE ts > datetime(\"now\", \"-1 hour\")'"

# Get latest timestamp
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "sqlite3 /opt/nifty-data-collector/nifty_local.db \
    'SELECT MAX(ts) FROM ltp_ticks'"
```

### **5.2 Database Queries**

```sql
-- Total records
SELECT COUNT(*) FROM ltp_ticks;

-- Latest 10 records
SELECT * FROM ltp_ticks ORDER BY ts DESC LIMIT 10;

-- NIFTY 50 data
SELECT * FROM ltp_ticks WHERE symbol = 'NIFTY 50' ORDER BY ts DESC LIMIT 100;

-- Options with full Greeks
SELECT * FROM ltp_ticks 
WHERE (symbol LIKE '%CE%' OR symbol LIKE '%PE%')
AND delta IS NOT NULL 
AND gamma IS NOT NULL
ORDER BY ts DESC LIMIT 100;

-- Data for specific date
SELECT * FROM ltp_ticks 
WHERE DATE(ts) = '2025-11-25'
ORDER BY ts;
```

---

## 🔐 **Section 6: Security Best Practices**

### **6.1 Credential Management**

```python
# Use environment variables (recommended)
import os
from dotenv import load_dotenv

load_dotenv()  # Load from .env file

VPS_HOST = os.getenv("VPS_HOST", "31.97.233.93")
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH")
API_TOKEN = os.getenv("HOSTINGER_API_TOKEN")
```

### **6.2 .env File Template**

```bash
# .env file (DO NOT COMMIT TO GIT)
VPS_HOST=31.97.233.93
VPS_USER=root
SSH_KEY_PATH=C:\Users\Ram\.ssh\nifty_server_key
HOSTINGER_API_TOKEN=E6Rm6hRne4WODcnDkegLcaedLCqYBRpTOZbNd04Z0afecf62
DB_PATH_VPS=/opt/nifty-data-collector/nifty_local.db
```

### **6.3 .gitignore Entry**

```
# Add to .gitignore
.env
*.db
*.sqlite
*.sqlite3
credentials.txt
```

---

## 📊 **Section 7: Data Usage Examples**

### **7.1 Backtesting Example**

```python
from nifty_data_fetcher import NiftyDataFetcher
import pandas as pd

fetcher = NiftyDataFetcher(ssh_key_path="~/.ssh/nifty_server_key")
fetcher.connect()

# Get historical data for backtesting
start_date = "2025-08-19T00:00:00"
end_date = "2025-11-25T23:59:59"
df = fetcher.get_data_by_date_range(start_date, end_date, symbol="NIFTY 50")

# Your backtesting logic
# ...

fetcher.disconnect()
```

### **7.2 Real-time Analysis Example**

```python
# Fetch latest data every minute
import time
from nifty_data_fetcher import NiftyDataFetcher

fetcher = NiftyDataFetcher(ssh_key_path="~/.ssh/nifty_server_key")
fetcher.connect()

while True:
    df = fetcher.get_latest_data(limit=1000)
    # Your real-time analysis
    print(f"Latest data: {len(df)} records")
    time.sleep(60)  # Wait 1 minute
```

---

## 🆘 **Section 8: Troubleshooting**

### **8.1 Common Issues**

**Issue: SSH Connection Failed**
```bash
# Check SSH key permissions (Linux/Mac)
chmod 600 ~/.ssh/nifty_server_key

# Test connection
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93
```

**Issue: Database Locked**
```bash
# Check if data collection service is running
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "supervisorctl status nifty-data-collector"
```

**Issue: No Data Returned**
```bash
# Check latest timestamp
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 \
    "sqlite3 /opt/nifty-data-collector/nifty_local.db \
    'SELECT MAX(ts) FROM ltp_ticks'"
```

---

## 📞 **Section 9: Support & Resources**

### **9.1 Important Paths**

```
VPS Database: /opt/nifty-data-collector/nifty_local.db
VPS Logs: /opt/nifty-data-collector/logs/
VPS Scripts: /opt/nifty-data-collector/scripts/
Market Calendar: /opt/nifty-data-collector/data/NSE_Market_Calendar_2025.csv
```

### **9.2 Useful Commands**

```bash
# Check service status
supervisorctl status

# View logs
tail -f /opt/nifty-data-collector/logs/nifty-data-collector.log

# Check database health
python3 /opt/nifty-data-collector/scripts/health_check.py
```

---

## ✅ **Quick Start Checklist**

- [ ] SSH key available at `$env:USERPROFILE\.ssh\nifty_server_key`
- [ ] Test SSH connection: `ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93`
- [ ] Install Python packages: `pip install paramiko pandas`
- [ ] Create `.env` file with credentials
- [ ] Test data fetch with sample query
- [ ] Verify data format matches your project needs

---

**📅 Last Updated:** November 26, 2025  
**🔧 Version:** 1.0.0  
**📊 Status:** Production Ready  
**🎯 Purpose:** Complete guide for fetching VPS data for external projects

