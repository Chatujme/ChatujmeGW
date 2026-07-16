"""IRC session: connection state, login lifecycle, room membership, command dispatch."""

import http.cookiejar
import json
import threading
import time
import traceback as tb
import urllib.request

from . import commands, config, numerics, state
from .api import ChatujmeAPI
from .models import Channel, ChannelMember, User
from .numerics import MOTD_LINES
from .util import log, sanitize_irc, sanitize_log


class ClientSession:
    def __init__(self, sock, address, handler):
        self.socket = sock
        self.address = address
        self.user = User()
        self.server_name = config.SERVER_NAME
        self.api = ChatujmeAPI(self)
        self.connection = True
        self.channels = []
        self.parent = handler
        self.cap_negotiating = False
        self.send_lock = threading.Lock()
        self.auth_attempted = False  # guards against double login (NICK/USER/PASS + CAP END)

    # --- outgoing IRC lines ---

    def send_numeric(self, code, msg):
        """Send IRC numeric reply"""
        line = f":{self.server_name} {code} {self.user.nick} {msg}\r\n"
        self.send_raw(line)

    def send_raw(self, msg):
        """Send a raw IRC line, byte-capped to the RFC 1459 512-byte limit."""
        if config.DEBUG:
            log(f">> {msg.strip()}")
        data = msg.encode('utf-8')
        # RFC 1459: a line is at most 512 bytes including CRLF. Cap on bytes, not
        # characters (a line of 'é' is twice as long in bytes), without splitting a
        # multibyte char at the boundary.
        if len(data) > config.MAX_LINE_LENGTH:
            body = data[:-2] if data.endswith(b"\r\n") else data
            body = body[:config.MAX_LINE_LENGTH - 2]
            # trailing bytes may be a truncated multibyte sequence - drop them
            body = body.decode('utf-8', errors='ignore').encode('utf-8')
            data = body + b"\r\n"
        try:
            # Lock + sendall: two threads (parse + MessagePoller) share this socket;
            # a bare send() may write partially and interleave lines
            with self.send_lock:
                self.socket.sendall(data)
        except Exception as e:
            if config.DEBUG:
                log(f"Send error: {e}")

    # --- login lifecycle ---

    def ping_keepalive(self, now):
        """Send periodic PING; returns False when the client missed the PONG deadline."""
        user = self.user
        if user.pending_ping_token is not None:
            # PING in flight - no new one until PONG arrives or deadline passes
            return (now - user.last_ping_sent) < user.pong_timeout
        if (now - user.last_ping_sent) >= user.ping_interval:
            ping_token = str(int(now))
            self.send_raw(f"PING :{ping_token}\r\n")
            user.last_ping_sent = now
            user.pending_ping_token = ping_token
        return True

    def reauthenticate(self):
        """Re-authenticate after session expiry (silent - no duplicate welcome)."""
        self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Session expired, attempting re-login...\r\n")
        self.user.login = self.authenticate(silent=True)
        if self.user.login:
            self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Re-login successful\r\n")
        else:
            self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Re-login failed, retrying later\r\n")
            time.sleep(10)
        return self.user.login

    def try_login(self):
        """Single guarded login entry point for NICK/USER/PASS/CAP END.

        Only attempts once per credential set, so the classic client sequence
        (NICK+USER+PASS then CAP END) doesn't fire authenticate() twice - which
        would relay the 2FA notice twice and burn two account login attempts.
        A newly supplied credential resets the guard (see reset_auth).
        """
        if self.user.login or self.auth_attempted:
            return
        if not (self.user.password and self.user.nick and self.user.username):
            return
        self.auth_attempted = True
        self.user.login = self.authenticate()

    def reset_auth(self):
        """A credential (re)arrived - allow one more login attempt."""
        self.auth_attempted = False

    def authenticate(self, silent=False):
        if not self.user.username or not self.user.nick or not self.user.password:
            if config.DEBUG:
                log(f"[LOGIN] Missing credentials: user={self.user.username}, nick={self.user.nick}")
            return False

        # Rate-limit login attempts: each hits the upstream check-login endpoint,
        # so an unthrottled client could brute-force passwords / amplify to the API
        if not self.command_allowed():
            self.send_raw(f":{self.server_name} NOTICE * :Too many login attempts. Slow down.\r\n")
            return False

        if config.DEBUG:
            log(f"[LOGIN] Attempting login for {self.user.username}")

        # Security: Use fresh in-memory cookies for each login (no file storage)
        self.user.cookie_jar = http.cookiejar.CookieJar()
        self.user.url_fetcher = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.user.cookie_jar)
        )

        try:
            response = self.api.authenticate(self.user.username, self.user.password)
            if config.DEBUG:
                log(f"[LOGIN] API response: {response[:200]}")  # sanitize_log handles password masking
            data = json.loads(response)

            # Security: Safe access to API response fields
            code = data.get('code', 0)
            message = data.get('message', 'Unknown error')

            if code in (200, 201):
                # Bind the session to the API-confirmed identity, not the client's
                # chosen NICK. Otherwise a client could log in as one account but set
                # someone else's nick and, via the JOIN ghost mechanism, force-close
                # that victim's connection (active_connections is keyed by nick).
                canonical = data.get('username')
                if canonical:
                    self.user.nick = canonical
                    self.user.username = canonical
                # Ghost mechanism deferred to JOIN (probes would kill real connections)
                if not silent:
                    self.send_welcome()
                log(f"User logged in: {self.user.username}")
                return True
            # 401 = bad credentials, 403 = 2FA required, anything else - relay server message
            self.send_numeric(numerics.ERR_NOLOGIN, f"{self.user.username} :{message}")
            return False
        except Exception as e:
            log(f"[LOGIN] Error: {e}")
            if config.DEBUG:
                tb.print_exc()
            return False

    def send_welcome(self):
        """Send proper RFC-compliant welcome sequence"""
        nick = self.user.nick

        # 001 RPL_WELCOME
        self.send_numeric(numerics.RPL_WELCOME, f":Welcome to Chatujme.cz IRC Gateway {nick}!{nick}@{self.server_name}")
        # 002 RPL_YOURHOST
        self.send_numeric(numerics.RPL_YOURHOST, f":Your host is {self.server_name}, running ChatujmeGW v{config.VERSION}")
        # 003 RPL_CREATED
        self.send_numeric(numerics.RPL_CREATED, ":This server was created for Chatujme.cz")
        # 004 RPL_MYINFO
        self.send_numeric(numerics.RPL_MYINFO, f"{self.server_name} ChatujmeGW-{config.VERSION} o o")

        # MOTD
        self.send_numeric(numerics.RPL_MOTDSTART, f":- {self.server_name} Message of the Day -")
        for line in MOTD_LINES:
            formatted = line.format(user=self.user.username, sex=self.user.sex, host=self.server_name, version=config.VERSION)
            self.send_numeric(numerics.RPL_MOTD, f":- {formatted}")
        self.send_numeric(numerics.RPL_ENDOFMOTD, ":End of /MOTD command")

        # Request client version via CTCP VERSION
        self.send_raw(f":{self.server_name} PRIVMSG {self.user.nick} :\x01VERSION\x01\r\n")

    # --- room membership ---

    def find_channel(self, channel_id):
        """Return the joined Channel with this id, or None."""
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            return None
        for channel in self.channels:
            if cid == int(channel.id):
                return channel
        return None

    def in_channel(self, channel_id):
        return self.find_channel(channel_id) is not None

    def fetch_channel_members(self, channel_id):
        """Fetch member list from the API; refresh the joined channel's members.

        On API failure get-users returns an error object ({code: 500}), not a
        list - treat anything that isn't a list as "no data" so the poller and
        NAMES/WHOIS don't crash on it.
        """
        try:
            data = json.loads(self.api.get_users(channel_id))
        except Exception:
            data = []
        if not isinstance(data, list):
            return []

        channel = self.find_channel(channel_id)
        if channel:
            channel.members = [self._make_member(entry) for entry in data]

        return data

    @staticmethod
    def _make_member(entry):
        member = ChannelMember()
        member.nick = entry["nick"]
        member.sex = entry["sex"]
        return member

    @staticmethod
    def field(value):
        """Sanitize an API-sourced string before framing it into an IRC line.

        CR/LF in a room name, topic, nick etc. would otherwise let a peer inject
        forged IRC lines into another user's client stream (the poller does the
        same for async messages).
        """
        return sanitize_irc(str(value))

    def send_names(self, channel_id):
        """Send RPL_NAMREPLY / RPL_ENDOFNAMES with fresh member data."""
        members = self.fetch_channel_members(channel_id)
        names = " ".join([f"{self.membership_prefix(m)}{self.field(m['nick'])}" for m in members])
        self.send_numeric(numerics.RPL_NAMREPLY, f"= #{channel_id} :{names}")
        self.send_numeric(numerics.RPL_ENDOFNAMES, f"#{channel_id} :End of /NAMES list")

    def part_channel(self, channel_id, notify_client=True):
        channel = self.find_channel(channel_id)
        if channel:
            self.channels.remove(channel)
        if notify_client:
            self.send_raw(f":{self.user.nick} PART #{channel_id}\r\n")
        # Always try to notify server about leaving
        try:
            self.api.part(channel_id)
        except Exception as e:
            if config.DEBUG:
                log(f"[PART] Error notifying server: {e}")

    def join_channel(self, channel_id):
        already_joined = self.in_channel(channel_id)
        data = self.api.join(channel_id)

        if config.DEBUG:
            log(f"JOIN to {channel_id}: {data}")

        # Security: Safe access to API response
        code = data.get('code', 0)

        if code == 403:
            # Banned from channel - send both RFC error and NOTICE with detailed message
            ban_msg = data.get('message', 'Cannot join channel')
            self.send_numeric(numerics.ERR_BANNEDFROMCHAN, f"#{data.get('id', channel_id)} :Cannot join channel (+b)")
            self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :{ban_msg}\r\n")
        elif code == 404:
            self.send_numeric(numerics.ERR_NOSUCHCHANNEL, f"#{channel_id} :No such channel")
            self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :{data.get('message', 'Room does not exist')}\r\n")
        elif code != 200:
            # Unknown error code - show as notice
            err_msg = data.get('message', f'Unknown error (code {code})')
            self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Error joining #{channel_id}: {err_msg}\r\n")
        else:
            # Ghost: activate connection and kill duplicates on first real JOIN
            nick_lower = self.user.nick.lower()
            with state.active_connections_lock:
                old_handler = state.active_connections.get(nick_lower)
                if old_handler is not None and old_handler is not self.parent:
                    log(f"[GHOST] Killing old connection for {self.user.nick} (new JOIN from {self.address})")
                    try:
                        old_handler.instance.send_raw(
                            f"ERROR :Closing link: {self.user.nick} (Overridden by new connection)\r\n"
                        )
                    except Exception:
                        pass
                    old_handler.running = False
                    if old_handler.instance:
                        old_handler.instance.connection = False
                        # Old cleanup must not part channels the new session is (re)joining
                        old_handler.instance.channels = []
                    try:
                        # Close the socket so the old thread's blocking recv exits now,
                        # not after the 300s timeout
                        old_handler.socket.close()
                    except Exception:
                        pass
                state.active_connections[nick_lower] = self.parent

            members = self.fetch_channel_members(channel_id)
            names = " ".join([f"{self.membership_prefix(m)}{self.field(m['nick'])}" for m in members])

            if not already_joined:
                channel = Channel()
                channel.id = int(data['id'])
                channel.idler_last_sent = time.time()
                self.channels.append(channel)

            # Send JOIN confirmation
            self.send_raw(f":{self.user.nick}!{self.user.nick}@{self.server_name} JOIN #{data['id']}\r\n")
            self.send_numeric(numerics.RPL_TOPIC, f"#{data['id']} :[{self.field(data['nazev'])}] {self.field(data['topic'])}")
            self.send_numeric(numerics.RPL_NAMREPLY, f"= #{data['id']} :{names}")
            self.send_numeric(numerics.RPL_ENDOFNAMES, f"#{data['id']} :End of /NAMES list")

    # --- helpers ---

    def make_hostmask(self, nick, channel_id):
        """Create nick!user@host format with sex info"""
        try:
            channel = self.find_channel(channel_id)
            if channel:
                for member in channel.members:
                    if member.nick == nick:
                        return f"{nick}!{nick}@{member.sex}"
            return f"{nick}!{nick}@users"
        except Exception:
            return f"{nick}!{nick}@users"

    def membership_prefix(self, member):
        """Channel membership prefix (modern IRC spec): @ op, % halfop, + voice."""
        if member.get('isOwner') or member.get('isOP'):
            return "@"
        elif member.get('isHalfOP'):
            return "%"
        elif member.get('sex') == "girls":
            return "+"
        return ""

    def send_text(self, text, room_id, target):
        # Rate limit only for messages to rooms (not internal calls)
        if not self.command_allowed():
            self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Rate limit exceeded. Slow down.\r\n")
            return {"code": 429, "message": "Rate limited"}
        response = self.api.post_text(room_id, text, target)
        try:
            return json.loads(response)
        except Exception:
            return []

    def command_allowed(self):
        """Check if command rate limit exceeded. Returns True if allowed."""
        now = time.time()
        # Clean old entries (older than 1 second)
        self.user.command_timestamps = [t for t in self.user.command_timestamps if now - t < 1.0]
        if len(self.user.command_timestamps) >= config.MAX_COMMANDS_PER_SECOND:
            return False
        self.user.command_timestamps.append(now)
        return True

    # --- incoming IRC lines ---

    def feed(self, data):
        if not data:
            self.connection = False
            return 2

        lines = data.replace("\r\n", "\n").split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Security: Limit line length
            if len(line) > config.MAX_LINE_LENGTH:
                log(f"[SECURITY] Line too long from {self.address} ({len(line)} chars), truncating")
                line = line[:config.MAX_LINE_LENGTH]

            parts = line.split(" ")
            command = parts[0].upper()

            if config.DEBUG:
                log(f"<< {sanitize_log(line)}")

            # Rate limit commands that hit the Chatujme API (messages are limited in send_text)
            if command in commands.RATE_LIMITED and not self.command_allowed():
                self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Rate limit exceeded. Slow down.\r\n")
                continue

            handler = commands.HANDLERS.get(command)
            if handler:
                # A malformed upstream API response inside a handler must not tear
                # down the whole client connection - contain it to this command
                try:
                    handler(self, parts, line)
                except Exception:
                    if config.DEBUG:
                        tb.print_exc()
                    self.send_raw(f":{self.server_name} NOTICE {self.user.nick} :Error processing {command}\r\n")
            else:
                self.send_numeric(numerics.ERR_UNKNOWNCOMMAND, f"{command} :Unknown command")
