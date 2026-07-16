"""Constants and runtime settings.

Runtime values (PORT, BIND, DEBUG, SSL_*) are overwritten once at startup
by the CLI entry point (chatujmegw.py); modules read them via `config.X`
so they always see the live value.
"""

import os
import sys

VERSION = "3.6.0"
SERVER_NAME = "chatujme.cz"  # server prefix used in IRC lines sent to clients
USER_AGENT = f'ChatujmeGW/v{VERSION} ({sys.platform} {os.name}) Python {sys.version.split(" ")[0]}'

# Security: Max retry attempts for API calls
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Validation limits (from Chatujme.cz registration)
MIN_NICK_LENGTH = 4
MAX_NICK_LENGTH = 23
MAX_ROOM_ID = 999999

# Security: Buffer and message limits (RFC 1459)
MAX_LINE_LENGTH = 512  # RFC 1459 max line length
MAX_BUFFER_SIZE = 4096  # Max buffer before disconnect
MAX_MESSAGE_LENGTH = 500  # Max message content length

# Security: Rate limiting (operators can tune via CHATUJMEGW_* env vars)
MAX_CONNECTIONS_PER_IP = int(os.environ.get("CHATUJMEGW_MAX_CONN_PER_IP", "5"))  # per window
CONNECTION_WINDOW = int(os.environ.get("CHATUJMEGW_CONN_WINDOW", "60"))  # seconds
MAX_COMMANDS_PER_SECOND = int(os.environ.get("CHATUJMEGW_MAX_CMD_PER_SEC", "10"))
MAX_CLIENTS = int(os.environ.get("CHATUJMEGW_MAX_CLIENTS", "378"))

# Security: API timeout
API_TIMEOUT = 10  # seconds

# Security: connection timeouts (seconds)
LOGIN_TIMEOUT = 60  # drop a connection that hasn't authenticated within this window
CLIENT_TIMEOUT = 300  # idle timeout once logged in (poller keepalive runs by then)

# Runtime settings (set by CLI at startup)
PORT = 6667
BIND = "127.0.0.1"  # Security: localhost only by default, use --listen 0.0.0.0 for external access
DEBUG = 0
VERBOSE_THREADS = False
SSL_ENABLED = False
SSL_PORT = None
SSL_CERT = None
SSL_KEY = None
DUAL_PORT_MODE = False
