# 🔐 VPS Access Guide for Other IDEs

**Last Updated:** January 3, 2026  
**VPS IP:** `31.97.233.93`  
**Username:** `root`  
**SSH Key:** `nifty_server_key`

---

## 📋 Quick Reference

### **SSH Connection Details**

| Parameter | Value |
|-----------|-------|
| **Host/IP** | `31.97.233.93` |
| **Username** | `root` |
| **Port** | `22` (default) |
| **SSH Key Name** | `nifty_server_key` |
| **Project Directory** | `/opt/nifty-data-collector` |
| **Database Path** | `/opt/nifty-data-collector/data/nifty_local.db` |

---

## 🪟 Windows (PowerShell)

### **Basic SSH Command**

```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93
```

### **SSH Key Location**

```
C:\Users\<YourUsername>\.ssh\nifty_server_key
```

### **Verify SSH Key Exists**

```powershell
Test-Path $env:USERPROFILE\.ssh\nifty_server_key
```

### **Test Connection**

```powershell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "echo 'Connection successful'"
```

---

## 🐧 Linux / macOS

### **Basic SSH Command**

```bash
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93
```

### **SSH Key Location**

```
~/.ssh/nifty_server_key
# or
/home/<username>/.ssh/nifty_server_key
```

### **Set Proper Permissions (Linux/macOS)**

```bash
chmod 600 ~/.ssh/nifty_server_key
```

### **Test Connection**

```bash
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "echo 'Connection successful'"
```

---

## 🔧 IDE-Specific Configuration

### **VS Code / Cursor**

#### **Option 1: Remote-SSH Extension**

1. Install the **Remote-SSH** extension
2. Press `F1` or `Ctrl+Shift+P`
3. Type: `Remote-SSH: Connect to Host`
4. Select: `Add New SSH Host`
5. Enter:
   ```
   ssh -i ~/.ssh/nifty_server_key root@31.97.233.93
   ```
   (Windows: `ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93`)

6. Save to SSH config file
7. Connect to the host

#### **Option 2: SSH Config File**

Create/edit `~/.ssh/config` (Linux/macOS) or `C:\Users\<username>\.ssh\config` (Windows):

```
Host nifty-vps
    HostName 31.97.233.93
    User root
    IdentityFile ~/.ssh/nifty_server_key
    # Windows: IdentityFile C:\Users\<username>\.ssh\nifty_server_key
```

Then connect with:
```bash
ssh nifty-vps
```

---

### **PyCharm / IntelliJ IDEA**

1. Go to **File → Settings → Tools → SSH Configurations**
2. Click **+** to add new configuration
3. Fill in:
   - **Host:** `31.97.233.93`
   - **Port:** `22`
   - **Username:** `root`
   - **Authentication type:** Key pair
   - **Private key file:** Browse to `nifty_server_key`
4. Click **Test Connection**
5. Click **OK**

---

### **Jupyter Notebook / JupyterLab**

#### **Using SSH Tunnel**

```python
import subprocess
import sqlite3
import pandas as pd

# SSH connection details
VPS_HOST = "31.97.233.93"
VPS_USER = "root"
SSH_KEY = "~/.ssh/nifty_server_key"  # Linux/macOS
# SSH_KEY = r"C:\Users\<username>\.ssh\nifty_server_key"  # Windows

# Execute remote SQL query
def query_vps_db(query):
    cmd = f'ssh -i {SSH_KEY} {VPS_USER}@{VPS_HOST} "sqlite3 /opt/nifty-data-collector/data/nifty_local.db \\"{query}\\""'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout

# Example usage
df = pd.read_sql_query("SELECT * FROM ltp_ticks LIMIT 10", 
                       query_vps_db)
```

---

### **RStudio / R**

```r
# Install required packages
# install.packages(c("ssh", "DBI", "RSQLite"))

library(ssh)
library(DBI)

# Connect via SSH
session <- ssh_connect("root@31.97.233.93", keyfile = "~/.ssh/nifty_server_key")

# Execute remote command
result <- ssh_exec_internal(session, 
  "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT COUNT(*) FROM ltp_ticks;'")

# Parse result
cat(rawToChar(result$stdout))
```

---

## 📊 Database Access Examples

### **Query Database via SSH (Windows PowerShell)**

```powershell
# Get record count
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT COUNT(*) FROM ltp_ticks;'"

# Get latest records
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT * FROM ltp_ticks ORDER BY ts DESC LIMIT 10;'"

# Get data for specific date
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT COUNT(*) FROM ltp_ticks WHERE date(ts) = \"2026-01-02\";'"
```

### **Query Database via SSH (Linux/macOS)**

```bash
# Get record count
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT COUNT(*) FROM ltp_ticks;'"

# Get latest records
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT * FROM ltp_ticks ORDER BY ts DESC LIMIT 10;'"

# Get data for specific date
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db 'SELECT COUNT(*) FROM ltp_ticks WHERE date(ts) = \"2026-01-02\";'"
```

---

## 📁 File Transfer (SCP)

### **Download Database (Windows PowerShell)**

```powershell
# Download entire database
scp -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93:/opt/nifty-data-collector/data/nifty_local.db "C:\path\to\local\backup.db"

# Download specific file
scp -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93:/opt/nifty-data-collector/nifty_stream_local_sqlite.py "C:\path\to\local\"
```

### **Download Database (Linux/macOS)**

```bash
# Download entire database
scp -i ~/.ssh/nifty_server_key root@31.97.233.93:/opt/nifty-data-collector/data/nifty_local.db ~/Downloads/backup.db

# Download specific file
scp -i ~/.ssh/nifty_server_key root@31.97.233.93:/opt/nifty-data-collector/nifty_stream_local_sqlite.py ~/Downloads/
```

### **Upload File to VPS (Windows PowerShell)**

```powershell
scp -i $env:USERPROFILE\.ssh\nifty_server_key "C:\path\to\file.py" root@31.97.233.93:/opt/nifty-data-collector/
```

### **Upload File to VPS (Linux/macOS)**

```bash
scp -i ~/.ssh/nifty_server_key ~/path/to/file.py root@31.97.233.93:/opt/nifty-data-collector/
```

---

## 🐍 Python Access Examples

### **Method 1: Using subprocess (Simple)**

```python
import subprocess
import json

def query_vps_db(query):
    """Execute SQL query on VPS database"""
    # Windows
    cmd = f'ssh -i $env:USERPROFILE\\.ssh\\nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db \\"{query}\\""'
    
    # Linux/macOS
    # cmd = f'ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "sqlite3 /opt/nifty-data-collector/data/nifty_local.db \\"{query}\\""'
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

# Example: Get record count
count = query_vps_db("SELECT COUNT(*) FROM ltp_ticks")
print(f"Total records: {count}")

# Example: Get latest data
latest = query_vps_db("SELECT * FROM ltp_ticks ORDER BY ts DESC LIMIT 5")
print(latest)
```

### **Method 2: Using paramiko (Advanced)**

```python
import paramiko
import sqlite3
import io

# SSH connection
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

# Windows
ssh.connect('31.97.233.93', username='root', 
            key_filename=r'C:\Users\<username>\.ssh\nifty_server_key')

# Linux/macOS
# ssh.connect('31.97.233.93', username='root', 
#             key_filename='~/.ssh/nifty_server_key')

# Execute command
stdin, stdout, stderr = ssh.exec_command(
    'sqlite3 /opt/nifty-data-collector/data/nifty_local.db "SELECT COUNT(*) FROM ltp_ticks;"'
)

result = stdout.read().decode()
print(f"Total records: {result}")

ssh.close()
```

### **Method 3: Using SSHTunnel + SQLite (Best for DataFrames)**

```python
from sshtunnel import SSHTunnelForwarder
import sqlite3
import pandas as pd

# Create SSH tunnel
with SSHTunnelForwarder(
    ('31.97.233.93', 22),
    ssh_username='root',
    ssh_pkey='~/.ssh/nifty_server_key',  # Linux/macOS
    # ssh_pkey=r'C:\Users\<username>\.ssh\nifty_server_key',  # Windows
    remote_bind_address=('127.0.0.1', 22)
) as tunnel:
    
    # Download database via SCP first, then connect locally
    # Or use SSH command execution
    pass
```

---

## 🔍 Common Commands

### **Check Service Status**

```bash
# Windows PowerShell
ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93 "supervisorctl status"

# Linux/macOS
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "supervisorctl status"
```

### **View Logs**

```bash
# Data collector logs
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "tail -50 /var/log/supervisor/nifty-data-collector.log"

# Telegram bot logs
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "tail -50 /var/log/supervisor/telegram-bot.log"
```

### **Check Database Size**

```bash
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "ls -lh /opt/nifty-data-collector/data/nifty_local.db"
```

### **List Project Files**

```bash
ssh -i ~/.ssh/nifty_server_key root@31.97.233.93 "ls -la /opt/nifty-data-collector/"
```

---

## ⚠️ Troubleshooting

### **Issue: Permission Denied (publickey)**

**Solution:**
1. Verify SSH key exists:
   - Windows: `Test-Path $env:USERPROFILE\.ssh\nifty_server_key`
   - Linux/macOS: `ls -la ~/.ssh/nifty_server_key`

2. Set correct permissions (Linux/macOS):
   ```bash
   chmod 600 ~/.ssh/nifty_server_key
   ```

3. Verify key format:
   ```bash
   head -1 ~/.ssh/nifty_server_key
   # Should show: -----BEGIN OPENSSH PRIVATE KEY----- or -----BEGIN RSA PRIVATE KEY-----
   ```

### **Issue: Connection Timeout**

**Solution:**
1. Check if VPS is running
2. Verify firewall settings
3. Try with verbose mode:
   ```bash
   ssh -v -i ~/.ssh/nifty_server_key root@31.97.233.93
   ```

### **Issue: Host Key Verification Failed**

**Solution:**
```bash
ssh -o StrictHostKeyChecking=no -i ~/.ssh/nifty_server_key root@31.97.233.93
```

---

## 📝 SSH Config File Template

Create `~/.ssh/config` (Linux/macOS) or `C:\Users\<username>\.ssh\config` (Windows):

```
Host nifty-vps
    HostName 31.97.233.93
    User root
    IdentityFile ~/.ssh/nifty_server_key
    # Windows: IdentityFile C:\Users\<username>\.ssh\nifty_server_key
    Port 22
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

Then simply use:
```bash
ssh nifty-vps
```

---

## 🔐 Security Notes

1. **Never commit SSH keys to Git**
2. **Keep SSH key permissions strict** (600 on Linux/macOS)
3. **Use SSH keys, not passwords**
4. **Rotate keys periodically**
5. **Use SSH config file for easier management**

---

## 📞 Quick Reference Card

```
VPS IP:        31.97.233.93
Username:      root
SSH Key:       nifty_server_key
Project Dir:   /opt/nifty-data-collector
Database:      /opt/nifty-data-collector/data/nifty_local.db

Windows:       ssh -i $env:USERPROFILE\.ssh\nifty_server_key root@31.97.233.93
Linux/macOS:   ssh -i ~/.ssh/nifty_server_key root@31.97.233.93
```

---

**Last Updated:** January 3, 2026

