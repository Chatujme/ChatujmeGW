"""TCP/SSL listeners and per-connection socket handling."""

import os
import select
import socket
import ssl
import sys
import threading
import time
import traceback as tb

from . import config, state
from .session import ClientSession
from .poller import ThreadJanitor, MessagePoller
from .state import allow_connection, refund_connection
from .util import log, fatal_error_pause


class ClientConnection(threading.Thread):
    PROBE_THRESHOLD = 5  # Connections shorter than 5s are considered probes

    def __init__(self, sock, address):
        threading.Thread.__init__(self)
        self.socket = sock
        self.address = address
        self.running = True
        self.daemon = True
        self.instance = None  # Set in run(), used by ghost mechanism
        self.connect_time = time.time()  # Track connection start for probe detection
        self.received_data = False  # did the client ever send anything (vs silent probe)
        self.recv_buffer = b""  # Security: raw byte buffer (decode per complete line)

    def run(self):
        log(f"Connection accepted from {self.address[0]}")
        instance = ClientSession(self.socket, self.address[0], self)
        self.instance = instance  # Store for ghost mechanism
        instance.send_raw(f":{instance.server_name} NOTICE * :Connected from {self.address[0]}, waiting for login.\r\n")

        while self.running:
            try:
                # Security: Read in smaller chunks. Buffer raw bytes and decode only
                # complete lines, so a multibyte UTF-8 char split across two recv()
                # chunks is not corrupted into replacement characters.
                chunk = instance.socket.recv(1024)
                if not chunk:
                    # Connection closed
                    break

                # Security: don't let unauthenticated clients hold a slot/thread for
                # the full 300s socket timeout (pre-auth slowloris). PING keepalive
                # only starts after login, so bound the login phase separately.
                if chunk:
                    self.received_data = True

                self.recv_buffer += chunk

                # Security: Check buffer size limit
                if len(self.recv_buffer) > config.MAX_BUFFER_SIZE:
                    log(f"[SECURITY] Buffer overflow attempt from {self.address[0]}, disconnecting")
                    instance.send_raw(f":{instance.server_name} ERROR :Buffer overflow - disconnecting\r\n")
                    break

                # Process complete lines only
                while b'\n' in self.recv_buffer:
                    n_pos = self.recv_buffer.find(b'\n')
                    raw_line = self.recv_buffer[:n_pos].rstrip(b'\r')
                    self.recv_buffer = self.recv_buffer[n_pos + 1:]
                    line = raw_line.decode('utf-8', errors='replace')

                    # Parse complete line
                    result = instance.feed(line + "\r\n")
                    if config.DEBUG:
                        log(f"[PARSE] result={result}, login={instance.user.login}, nick={instance.user.nick}")
                    if result == 2:
                        if config.DEBUG:
                            log("[PARSE] Breaking due to result=2")
                        self.running = False
                        break

            except socket.timeout:
                # Drop connections that never authenticated within the login window
                # instead of holding a slot for the full socket timeout
                if not instance.user.login and (time.time() - self.connect_time) >= config.LOGIN_TIMEOUT:
                    log(f"[TIMEOUT] {self.address[0]} did not log in within {config.LOGIN_TIMEOUT}s")
                    break
                continue
            except Exception as e:
                log(f"Connection from {self.address[0]} closed: {e}")
                if config.DEBUG:
                    tb.print_exc()
                # Leave all rooms on disconnect - don't send to client (already disconnected)
                for channel in instance.channels[:]:
                    instance.part_channel(channel.id, notify_client=False)
                instance.connection = False
                break

            if instance.user.nick and instance.user.login and not instance.user.polling:
                try:
                    # Login done: relax the short pre-auth recv timeout to the full
                    # idle timeout (poller keepalive takes over from here)
                    try:
                        self.socket.settimeout(config.CLIENT_TIMEOUT)
                    except Exception:
                        pass
                    with state.threads_lock:
                        state.threads.append(MessagePoller(instance))
                    state.janitor.start_threads()
                    instance.user.polling = True
                except Exception as e:
                    if config.DEBUG:
                        tb.print_exc()
                    break

        # Cleanup on disconnect
        for channel in instance.channels[:]:
            instance.part_channel(channel.id, notify_client=False)
        instance.connection = False

        # Remove from active connections registry
        if instance.user.nick:
            nick_lower = instance.user.nick.lower()
            with state.active_connections_lock:
                if state.active_connections.get(nick_lower) is self:
                    del state.active_connections[nick_lower]

        # Probe-friendly rate limiting: refund the budget ONLY for genuine silent
        # probes (health checks that read the greeting and close without sending).
        # Requiring received_data == False closes the refund abuse where an attacker
        # churns sub-5s connections to evade the per-IP limit.
        connection_duration = time.time() - self.connect_time
        if connection_duration < self.PROBE_THRESHOLD and not self.received_data:
            refund_connection(self.address[0])

        log(f"Connection from {self.address[0]} closed.")
        try:
            self.socket.close()
        except Exception:
            pass


def create_server_socket(bind_addr, port, description=""):
    """Create and configure a server socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Platform-specific socket options
    if sys.platform == 'win32':
        # Windows: SO_EXCLUSIVEADDRUSE prevents port hijacking
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # Linux/Mac: SO_REUSEADDR allows quick restart after crash
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    s.setblocking(False)  # Non-blocking for select()

    try:
        s.bind((bind_addr, port))
    except OSError as e:
        if e.errno == 10048 or e.errno == 98:  # Windows WSAEADDRINUSE / Linux EADDRINUSE
            log(f"ERROR: Port {port} is already in use{description}. Another instance running?")
        else:
            log(f"ERROR: Cannot bind to {bind_addr}:{port}{description} - {e}")
        return None

    s.listen(50)
    return s


def main():
    # SSL/TLS context setup
    ssl_context = None
    ssl_required = config.SSL_ENABLED or config.DUAL_PORT_MODE

    if ssl_required:
        if not config.SSL_CERT or not config.SSL_KEY:
            log("ERROR: SSL enabled but --ssl-cert and --ssl-key are required")
            fatal_error_pause()
            sys.exit(1)

        if not os.path.exists(config.SSL_CERT):
            log(f"ERROR: SSL certificate not found: {config.SSL_CERT}")
            fatal_error_pause()
            sys.exit(1)

        if not os.path.exists(config.SSL_KEY):
            log(f"ERROR: SSL key not found: {config.SSL_KEY}")
            fatal_error_pause()
            sys.exit(1)

        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2  # Security: Disable old protocols
            ssl_context.load_cert_chain(config.SSL_CERT, config.SSL_KEY)
            log(f"SSL/TLS enabled with certificate: {config.SSL_CERT}")
        except ssl.SSLError as e:
            log(f"ERROR: Failed to load SSL certificate: {e}")
            fatal_error_pause()
            sys.exit(1)

    # Create server sockets
    server_sockets = []
    socket_info = {}  # Maps socket to (port, use_ssl)

    if config.DUAL_PORT_MODE:
        # Dual-port mode: plain on config.PORT, SSL on config.SSL_PORT
        s_plain = create_server_socket(config.BIND, config.PORT, " (plain)")
        if not s_plain:
            fatal_error_pause()
            sys.exit(1)
        server_sockets.append(s_plain)
        socket_info[s_plain] = (config.PORT, False)

        s_ssl = create_server_socket(config.BIND, config.SSL_PORT, " (SSL)")
        if not s_ssl:
            s_plain.close()
            fatal_error_pause()
            sys.exit(1)
        server_sockets.append(s_ssl)
        socket_info[s_ssl] = (config.SSL_PORT, True)

        log(f"ChatujmeGW {config.VERSION} (Python 3), dual-port mode:")
        log(f"  Plain IRC on {config.BIND}:{config.PORT}")
        log(f"  SSL/TLS IRC on {config.BIND}:{config.SSL_PORT}")
    else:
        # Single port mode
        s = create_server_socket(config.BIND, config.PORT)
        if not s:
            fatal_error_pause()
            sys.exit(1)
        server_sockets.append(s)
        socket_info[s] = (config.PORT, config.SSL_ENABLED)

        ssl_status = " [SSL/TLS]" if config.SSL_ENABLED else ""
        log(f"ChatujmeGW {config.VERSION} (Python 3), listening on {config.BIND}:{config.PORT}{ssl_status}")

    state.janitor = ThreadJanitor()
    state.janitor.start()

    try:
        while state.janitor.running:
            try:
                # Use select to wait for connections on any socket
                readable, _, _ = select.select(server_sockets, [], [], 1.0)

                for server_socket in readable:
                    try:
                        connection, address = server_socket.accept()
                    except BlockingIOError:
                        continue

                    port, use_ssl = socket_info[server_socket]

                    # Security: Check rate limit per IP
                    if not allow_connection(address[0]):
                        log(f"[SECURITY] Rate limit exceeded for {address[0]}, rejecting connection")
                        try:
                            connection.send(b"ERROR :Too many connections from your IP. Try again later.\r\n")
                        except Exception:
                            pass
                        connection.close()
                        continue

                    # Wrap connection with SSL if this socket requires it. Bound the
                    # handshake first: a stalled TLS handshake would otherwise block
                    # the whole accept loop (no new clients accepted) since it runs
                    # inline here.
                    if use_ssl and ssl_context:
                        try:
                            connection.settimeout(config.LOGIN_TIMEOUT)
                            connection = ssl_context.wrap_socket(connection, server_side=True)
                            if config.DEBUG:
                                log(f"[SSL] Secure connection established with {address[0]}:{port}")
                        except (ssl.SSLError, socket.timeout, OSError) as e:
                            log(f"[SSL] Handshake failed for {address[0]}: {e}")
                            try:
                                connection.close()
                            except Exception:
                                pass
                            continue

                    # Short timeout until the client logs in (bounds pre-auth
                    # slowloris); ClientConnection raises it after a successful login
                    connection.settimeout(config.LOGIN_TIMEOUT)
                    with state.threads_lock:
                        # Security: Max connections limit (count only client handlers,
                        # state.threads also holds one MessagePoller thread per logged-in user)
                        client_count = sum(1 for t in state.threads if isinstance(t, ClientConnection))
                        if client_count < config.MAX_CLIENTS:
                            handler = ClientConnection(connection, address)
                            state.threads.append(handler)
                        else:
                            log(f"[SECURITY] Max connections reached, rejecting {address[0]}")
                            try:
                                connection.send(b"ERROR :Server is full. Try again later.\r\n")
                            except Exception:
                                pass
                            connection.close()
                            continue
                    state.janitor.start_threads()

            except Exception as e:
                if config.DEBUG:
                    log(f"Accept error: {e}")
    except KeyboardInterrupt:
        log("Received shutdown signal...")
    finally:
        state.janitor.running = False
        for s in server_sockets:
            try:
                s.close()
            except Exception:
                pass
        # Wait for threads to finish (graceful shutdown)
        shutdown_timeout = 5
        start_time = time.time()
        while state.threads and (time.time() - start_time) < shutdown_timeout:
            time.sleep(0.1)
        log("Shutting down...")
