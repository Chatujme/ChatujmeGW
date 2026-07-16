"""Process-wide shared state: thread registry, per-IP rate limiting, nick registry."""

import threading
import time

from . import config

# All running gateway threads (client connections + message pollers)
threads = []
threads_lock = threading.Lock()

# The janitor thread that starts and reaps entries in `threads` (set by server.main)
janitor = None

# Per-IP connection rate limiting: IP -> list of connection timestamps
connection_log = {}
connection_log_lock = threading.Lock()

# Active connections registry: nick (lowercase) -> ClientConnection instance
# Used for duplicate nick detection (ghost) and PONG timeout enforcement
active_connections = {}
active_connections_lock = threading.Lock()


def allow_connection(ip):
    """
    Record a connection attempt; returns False when the IP exceeded
    the per-window connection rate limit.
    """
    now = time.time()
    with connection_log_lock:
        if ip not in connection_log:
            connection_log[ip] = []

        # Clean old entries
        connection_log[ip] = [t for t in connection_log[ip] if now - t < config.CONNECTION_WINDOW]

        if len(connection_log[ip]) >= config.MAX_CONNECTIONS_PER_IP:
            return False

        connection_log[ip].append(now)
        return True


def refund_connection(ip):
    """
    Remove one rate-limit entry for an IP.
    Called when a short-lived connection (probe) disconnects quickly,
    so that health-check probes don't exhaust the rate limit budget.
    """
    with connection_log_lock:
        entries = connection_log.get(ip)
        if entries:
            entries.pop()  # remove the most recent entry
            if not entries:
                del connection_log[ip]  # don't leak empty keys under IP churn
