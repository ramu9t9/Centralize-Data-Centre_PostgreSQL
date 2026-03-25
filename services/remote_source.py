"""
Remote tick data source selection.

- If REMOTE_DATABASE_URL (or SYNC_SOURCE_DATABASE_URL) is set: sync reads from that
  PostgreSQL database (e.g. HostITSmart).

- Many VPS providers block port 5432 from the public internet. Set REMOTE_PG_SSH_TUNNEL=1
  to open an SSH local forward (uses services/ssh_vps.py: VPS_HOST, VPS_SSH_PORT, key)
  and connect via 127.0.0.1:REMOTE_PG_TUNNEL_LOCAL_PORT (default 15432).

- If REMOTE_DATABASE_URL is unset: sync uses VPS_HOST + SSH + SQLite (Hostinger-style).
"""

import atexit
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse, urlunparse

# Match services/db.py: load .env before reading REMOTE_DATABASE_URL
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_path)
    except ImportError:
        pass

import psycopg2

REMOTE_DATABASE_URL = (
    os.getenv("REMOTE_DATABASE_URL", "").strip()
    or os.getenv("SYNC_SOURCE_DATABASE_URL", "").strip()
)

_DO_TRUE = frozenset({"1", "true", "yes", "on"})


def _use_ssh_tunnel() -> bool:
    return os.getenv("REMOTE_PG_SSH_TUNNEL", "").strip().lower() in _DO_TRUE


def ssh_tunnel_enabled() -> bool:
    """True when REMOTE_PG_SSH_TUNNEL is set (sync will use SSH -L for Postgres)."""
    return _use_ssh_tunnel()


def uses_remote_postgres() -> bool:
    return bool(REMOTE_DATABASE_URL)


_tunnel_proc: Optional[subprocess.Popen] = None


def _port_is_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _rewrite_url_to_localhost_port(original_url: str, local_port: int) -> str:
    """Same credentials/db path, host 127.0.0.1 and given port (for SSH tunnel)."""
    p = urlparse(original_url)
    user = p.username or ""
    password = p.password or ""
    if password:
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    elif user:
        auth = f"{quote(user, safe='')}@"
    else:
        auth = ""
    new_netloc = f"{auth}127.0.0.1:{local_port}"
    return urlunparse((p.scheme, new_netloc, p.path, p.params, p.query, p.fragment))


def _stop_tunnel():
    global _tunnel_proc
    if _tunnel_proc is None:
        return
    if _tunnel_proc.poll() is None:
        _tunnel_proc.terminate()
        try:
            _tunnel_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tunnel_proc.kill()
    _tunnel_proc = None


atexit.register(_stop_tunnel)


def ensure_ssh_tunnel_for_remote_pg() -> None:
    """
    Start ssh -L local_port:remote_target (if needed). remote_target is on the VPS
    (default 127.0.0.1:5432 where Postgres listens).
    """
    global _tunnel_proc

    local_port = int(os.getenv("REMOTE_PG_TUNNEL_LOCAL_PORT", "15432").strip() or "15432")
    remote_target = os.getenv("REMOTE_PG_TUNNEL_TARGET", "127.0.0.1:5432").strip()
    if not remote_target:
        remote_target = "127.0.0.1:5432"

    # Only reuse an existing forward if *our* SSH child is still running. A stale
    # listener or another app on local_port must not skip starting the tunnel.
    if _tunnel_proc is not None and _tunnel_proc.poll() is None:
        return

    if _tunnel_proc is not None and _tunnel_proc.poll() is not None:
        _tunnel_proc = None

    if _port_is_open("127.0.0.1", local_port):
        raise RuntimeError(
            f"127.0.0.1:{local_port} is already in use. Set REMOTE_PG_TUNNEL_LOCAL_PORT "
            "to a free port, stop the conflicting process, or close a manual ssh -L tunnel."
        )

    from services.ssh_vps import ssh_base_argv, ssh_user_host

    forward_spec = f"{local_port}:{remote_target}"
    cmd = ssh_base_argv() + [
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "ConnectTimeout=20",
        "-N",
        "-L",
        forward_spec,
        ssh_user_host(),
    ]

    _tunnel_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    deadline = time.time() + 45.0
    while time.time() < deadline:
        if _tunnel_proc.poll() is not None:
            err = b""
            if _tunnel_proc.stderr:
                err = _tunnel_proc.stderr.read() or b""
            msg = err.decode(errors="replace").strip()[:800]
            _tunnel_proc = None
            raise RuntimeError(
                "SSH tunnel failed (ssh exited). Check VPS_HOST, VPS_SSH_PORT, key, and that "
                f"Postgres is reachable on the VPS at {remote_target}. stderr: {msg or '(empty)'}"
            )
        if _port_is_open("127.0.0.1", local_port, timeout=0.2):
            return
        time.sleep(0.2)

    _stop_tunnel()
    raise RuntimeError(
        f"SSH tunnel did not open 127.0.0.1:{local_port} in time. "
        "Try another REMOTE_PG_TUNNEL_LOCAL_PORT if the port is already in use."
    )


def reset_ssh_tunnel():
    """Tear down the SSH forward subprocess so the next connect_remote() starts a fresh tunnel."""
    _stop_tunnel()


def connect_remote():
    """Open a psycopg2 connection to the remote PostgreSQL sync source."""
    if not REMOTE_DATABASE_URL:
        raise RuntimeError(
            "REMOTE_DATABASE_URL (or SYNC_SOURCE_DATABASE_URL) is not set"
        )

    url = REMOTE_DATABASE_URL
    if _use_ssh_tunnel():
        local_port = int(os.getenv("REMOTE_PG_TUNNEL_LOCAL_PORT", "15432").strip() or "15432")
        ensure_ssh_tunnel_for_remote_pg()
        url = _rewrite_url_to_localhost_port(REMOTE_DATABASE_URL, local_port)

    conn_kw = {"connect_timeout": 60}
    if os.getenv("REMOTE_PG_TCP_KEEPALIVE", "1").strip().lower() in _DO_TRUE:
        conn_kw.update(
            keepalives=1,
            keepalives_idle=int(os.getenv("REMOTE_PG_KEEPALIVES_IDLE", "30").strip() or "30"),
            keepalives_interval=int(os.getenv("REMOTE_PG_KEEPALIVES_INTERVAL", "10").strip() or "10"),
            keepalives_count=int(os.getenv("REMOTE_PG_KEEPALIVES_COUNT", "6").strip() or "6"),
        )
    return psycopg2.connect(url, **conn_kw)
