"""Process-wide shared state: thread registry, per-IP rate limiting, nick registry."""

import threading
import time

from . import config

# Thread synchronization
thread_lock = threading.Lock()

# Rate limiting storage
connection_counts = {}  # IP -> list of connection timestamps
connection_counts_lock = threading.Lock()

# Active connections registry: nick (lowercase) -> SocketHandler instance
# Used for duplicate nick detection (ghost) and PONG timeout enforcement
active_connections = {}  # nick_lower -> SocketHandler
active_connections_lock = threading.Lock()


class World:
    vlakna = []
    collector = None


def check_rate_limit(ip):
    """
    Check if IP has exceeded connection rate limit.
    Returns True if connection is allowed, False if rate limited.
    """
    now = time.time()
    with connection_counts_lock:
        if ip not in connection_counts:
            connection_counts[ip] = []

        # Clean old entries
        connection_counts[ip] = [t for t in connection_counts[ip] if now - t < config.CONNECTION_WINDOW]

        if len(connection_counts[ip]) >= config.MAX_CONNECTIONS_PER_IP:
            return False

        connection_counts[ip].append(now)
        return True


def uncount_rate_limit(ip):
    """
    Remove one rate-limit entry for an IP.
    Called when a short-lived connection (probe) disconnects quickly,
    so that health-check probes don't exhaust the rate limit budget.
    """
    with connection_counts_lock:
        entries = connection_counts.get(ip)
        if entries:
            entries.pop()  # remove the most recent entry
