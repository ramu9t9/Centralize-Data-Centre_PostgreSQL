"""
Shared SSH settings for VPS access (SQLite collector over OpenSSH).

Set in .env:
  VPS_HOST, VPS_USER, VPS_SSH_PORT (default 22), SSH_KEY_PATH

Examples:
  Hostinger:  VPS_HOST=31.97.233.93   VPS_SSH_PORT=22 (or omit)
  HostITSmart: VPS_HOST=103.168.18.35 VPS_SSH_PORT=7576
"""

import os
from pathlib import Path

_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_path)
    except ImportError:
        pass

VPS_HOST = os.getenv("VPS_HOST", "31.97.233.93")
VPS_USER = os.getenv("VPS_USER", "root")
SSH_KEY_PATH = os.path.expanduser(os.getenv("SSH_KEY_PATH", r"~\.ssh\nifty_server_key"))
VPS_SSH_PORT = os.getenv("VPS_SSH_PORT", "22").strip() or "22"


def ssh_base_argv():
    """['ssh', '-i', key] plus ['-p', PORT] when port is not 22."""
    argv = ["ssh", "-i", SSH_KEY_PATH]
    if VPS_SSH_PORT != "22":
        argv.extend(["-p", VPS_SSH_PORT])
    return argv


def ssh_user_host() -> str:
    return f"{VPS_USER}@{VPS_HOST}"


def scp_base_argv():
    """['scp', '-i', key] plus ['-P', PORT] when port is not 22 (OpenSSH uses -P for scp)."""
    argv = ["scp", "-i", SSH_KEY_PATH]
    if VPS_SSH_PORT != "22":
        argv.extend(["-P", VPS_SSH_PORT])
    return argv
