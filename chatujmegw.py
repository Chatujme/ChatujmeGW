#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
  IRC Gateway for Chatujme.cz chat
  Based on lidegw v46 ( http://sourceforge.net/projects/lidegw/ )

  Refactored for Python 3 and RFC compliance

  @license MIT
  @author LuRy <lury@lury.cz>, <lury@chatujme.cz>

  rfc-codes https://www.alien.net.au/irc/irc2numerics.html
  rfc https://tools.ietf.org/html/rfc1459
"""

import io
import os
import re
import select
import socket
import ssl
import sys
import threading
import time
import urllib.request
import urllib.parse
import random
import json
import http.cookiejar
import argparse
import traceback as tb

# Force UTF-8 output on Windows to support emoji and special characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PORT = 6667
BIND = "0.0.0.0"  # Overridden by --listen
VERSION = "3.0.5"
UA = f'ChatujmeGW/v{VERSION} ({sys.platform} {os.name}) Python {sys.version.split(" ")[0]}'

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

# Security: Rate limiting
MAX_CONNECTIONS_PER_IP = 5  # Max simultaneous connections per IP
CONNECTION_WINDOW = 60  # Rate limit window in seconds
MAX_COMMANDS_PER_SECOND = 10  # Max commands per second per connection
MAX_CLIENTS = 378  # Max simultaneous client connections

# Security: API timeout
API_TIMEOUT = 10  # Reduced from 30 seconds

# Thread synchronization
thread_lock = threading.Lock()

# Rate limiting storage
connection_counts = {}  # IP -> list of connection timestamps
connection_counts_lock = threading.Lock()

# Active connections registry: nick (lowercase) -> SocketHandler instance
# Used for duplicate nick detection (ghost) and PONG timeout enforcement
active_connections = {}  # nick_lower -> SocketHandler
active_connections_lock = threading.Lock()


def sanitize_irc(text):
    """
    Remove CRLF, null bytes and other control characters from IRC messages.
    Prevents IRC protocol injection attacks.
    Preserves \x01 (CTCP marker) and \t (tab).
    """
    if not text:
        return ""
    # Remove CR, LF, null bytes
    text = text.replace('\r', '').replace('\n', '').replace('\0', '')
    # Remove other potentially dangerous control characters (0x00-0x1F except CTCP marker and tab)
    return ''.join(c for c in text if ord(c) >= 32 or c == '\t' or c == '\x01')


def sanitize_log(text):
    """
    Sanitize sensitive data from log output.
    Masks passwords, tokens, and session data.
    """
    if not text:
        return ""
    text = str(text)
    # Mask password in various formats
    text = re.sub(r'password=[^&\s"\']+', 'password=***', text, flags=re.IGNORECASE)
    text = re.sub(r'"password"\s*:\s*"[^"]+"', '"password": "***"', text, flags=re.IGNORECASE)
    text = re.sub(r'PASS\s+\S+', 'PASS ***', text, flags=re.IGNORECASE)
    # Mask tokens and session IDs
    text = re.sub(r'token=[^&\s"\']+', 'token=***', text, flags=re.IGNORECASE)
    text = re.sub(r'session[_-]?id=[^&\s"\']+', 'session_id=***', text, flags=re.IGNORECASE)
    text = re.sub(r'cookie:\s*[^\r\n]+', 'cookie: ***', text, flags=re.IGNORECASE)
    return text


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
        connection_counts[ip] = [t for t in connection_counts[ip] if now - t < CONNECTION_WINDOW]

        if len(connection_counts[ip]) >= MAX_CONNECTIONS_PER_IP:
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


def validate_nick(nick):
    """
    Validate nickname according to Chatujme.cz rules:
    - Only a-z, 0-9, dash (-), underscore (_)
    - Must NOT start with number, dash, underscore, or dot
    - Length: 4-23 characters
    """
    if not nick:
        return False
    if len(nick) < MIN_NICK_LENGTH or len(nick) > MAX_NICK_LENGTH:
        return False
    # Must not start with number, dash, underscore, or dot
    if not re.match(r'^[^0-9\-_\.][a-zA-Z0-9\-_\.]*$', nick):
        return False
    # Overall pattern: only alphanumeric, dash, underscore
    return bool(re.match(r'^[a-zA-Z0-9\-_]+$', nick))


def validate_room_id(room_id):
    """Validate room ID - must be numeric and within range"""
    try:
        rid = int(room_id)
        return 0 < rid <= MAX_ROOM_ID
    except (ValueError, TypeError):
        return False


parser = argparse.ArgumentParser(description=f'ChatujmeGW - v{VERSION}')
parser.add_argument('--port', type=int, help="Default port 6667", default=6667)
parser.add_argument('--listen', help="Bind gateway. Default 0.0.0.0", default="0.0.0.0")
parser.add_argument('--debug', help="Debug/Verbose print", type=int, default=0)
parser.add_argument('--ssl', action='store_true', help="Enable SSL/TLS encryption (SSL-only mode)")
parser.add_argument('--ssl-port', type=int, help="Additional SSL port (enables dual-port mode: non-SSL on --port, SSL on --ssl-port)")
parser.add_argument('--ssl-cert', help="Path to SSL certificate file (PEM format)")
parser.add_argument('--ssl-key', help="Path to SSL private key file (PEM format)")
args = parser.parse_args()

PORT = args.port
BIND = args.listen
DEBUG = args.debug
VERBOSE_THREADS = DEBUG >= 2
SSL_ENABLED = args.ssl
SSL_PORT = args.ssl_port
SSL_CERT = args.ssl_cert
SSL_KEY = args.ssl_key
DUAL_PORT_MODE = SSL_PORT is not None

try:
    PATH = os.path.dirname(os.path.abspath(__file__))
except NameError:
    PATH = os.path.dirname(os.path.abspath(sys.argv[0]))


# RFC numeric codes
class IRC_RFC:
    RPL_WELCOME = "001"
    RPL_YOURHOST = "002"
    RPL_CREATED = "003"
    RPL_MYINFO = "004"
    RPL_VERSION = "351"
    RPL_MOTDSTART = "375"
    RPL_MOTD = "372"
    RPL_ENDOFMOTD = "376"
    RPL_LISTSTART = "321"
    RPL_LIST = "322"
    RPL_LISTEND = "323"
    RPL_TOPIC = "332"
    RPL_NOTOPIC = "331"
    ERR_NOSUCHNICK = "401"
    ERR_NOSUCHCHANNEL = "403"
    ERR_UNKNOWNCOMMAND = "421"
    ERR_NEEDMOREPARAMS = "461"
    ERR_NOLOGIN = "444"
    ERR_BANNEDFROMCHAN = "474"
    RPL_CHANNELMODEIS = "324"
    RPL_WHOREPLY = "352"
    RPL_ENDOFWHO = "315"
    RPL_NAMREPLY = "353"
    RPL_ENDOFNAMES = "366"
    RPL_USERHOST = "302"
    RPL_WHOISUSER = "311"
    RPL_WHOISCHANNELS = "319"
    RPL_WHOISSERVER = "312"
    RPL_WHOISHOST = "378"
    RPL_WHOISIDLE = "317"
    RPL_ENDOFWHOIS = "318"
    RPL_AWAY = "301"
    RPL_UNAWAY = "305"
    RPL_NOWAWAY = "306"
    RPL_NOTICE = "NOTICE"
    RPL_JOIN = "JOIN"
    RPL_PART = "PART"
    RPL_MODE = "MODE"
    RPL_KICK = "KICK"
    RPL_PRIVMSG = "PRIVMSG"


# MOTD lines (without empty lines for RFC compliance)
MOTD_LINES = [
    "  .g8\"\"\"bgd` MM             Welcome to Chatujme.cz",
    ".dP'     `M  MM             Logged in as {user}@{sex}.{host}",
    "dM'       `  MMpMMMb.",
    "MM           MM    MM       Gateway version {version}",
    "MM.          MM    MM",
    "`Mb.     ,'  MM    MM",
    "  `\"bmmmd' .JMML  JMML.",
]


class World:
    vlakna = []
    collector = None


class User:
    def __init__(self):
        self.username = ""
        self.nick = ""
        self.password = ""
        self.me = "chatujme.cz"
        self.login = False
        self.sex = "boys"
        self.reading = False
        # Security: Use in-memory cookies only - no file storage
        self.cookie_jar = http.cookiejar.CookieJar()
        self.url_fetcher = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        # Security: Command rate limiting per connection
        self.command_timestamps = []
        self.last_command_check = 0
        self.settings_show_pm_from = True
        self.timer = 5
        self.idler_enable = False
        self.idler_timer = 2400  # 40min
        self.idler_text = [".", "..", "AFK"]
        self.show_smiles = 1  # 0 - Hide, 1 - Text, 2 - URL
        self.away_message = None  # None = not away, string = away message
        self.away_last_sent = 0  # Timestamp of last away message sent
        self.away_interval = 1800  # 30 minutes
        self.last_ping_sent = 0  # Timestamp of last server PING
        self.ping_interval = 60  # Send PING every 60 seconds
        self.last_pong_received = 0  # Timestamp of last PONG from client
        self.pong_timeout = 120  # Disconnect if no PONG within 120 seconds
        self.pending_ping_token = None  # Token of the last PING sent (for matching)
        self.client_version = None  # IRC client version from CTCP VERSION reply


class UserInRoom:
    def __init__(self):
        self.nick = ""
        self.sex = ""


class RoomStruct:
    def __init__(self):
        self.id = None
        self.nick = ""
        self.users = []
        self.last_id = 0
        self.last_mess = ""
        self.first_load = True
        self.idler_lastsend = 0
        self.say_ago_seconds = 0  # Server-side idle time from API


def log(text, sanitize=True):
    """Log message with optional sanitization of sensitive data"""
    text = str(text).replace("\r", "").replace("\n", " | ")
    if sanitize:
        text = sanitize_log(text)
    print(f"[{time.strftime('%Y/%m/%d %H:%M:%S')}] {text}", flush=True)


def fatal_error_pause():
    """On Windows, pause before exit so user can read error messages"""
    if sys.platform == 'win32':
        log("Closing in 10 seconds...")
        time.sleep(10)


class Collector(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.running = True
        self.daemon = True
        if DEBUG and VERBOSE_THREADS:
            log("Collector initialized")

    def run(self):
        if DEBUG and VERBOSE_THREADS:
            log("Collector started")
        while self.running:
            with thread_lock:
                thread_count = len(World.vlakna)
                for thread in World.vlakna[:]:
                    if not thread.is_alive() and thread._started.is_set():
                        World.vlakna.remove(thread)
                        if DEBUG and VERBOSE_THREADS:
                            log(f"Collector purging {thread}")
                        thread_count -= 1
            if DEBUG and VERBOSE_THREADS:
                log(f"Collector: all clear ({thread_count} threads)")
            time.sleep(5)

        # shutdown
        with thread_lock:
            for thread in World.vlakna:
                thread.running = False
        if DEBUG:
            log("Collector shutdown")

    def start_threads(self):
        with thread_lock:
            try:
                for thread in World.vlakna:
                    if not thread._started.is_set():
                        thread.start()
            except Exception as e:
                log(f"Thread failed to start: {e}")


class GetMessages(threading.Thread):
    def __init__(self, inst, sock):
        threading.Thread.__init__(self)
        self.inst = inst
        self.running = True
        self.daemon = True

    def run(self):
        while self.running and self.inst.connection:
            if len(self.inst.rooms) == 0:
                time.sleep(5)
                continue
            if not self.inst.connection:
                return

            for room in self.inst.rooms[:]:  # Copy list to allow modification during iteration
                try:
                    response = self.inst.get_url(
                        f"{self.inst.system.url}/get-messages?id={room.id}&from={int(room.last_id)}"
                    )
                    data = json.loads(response)

                    # Debug: log every API response structure
                    if DEBUG >= 2:
                        log(f"[GET-MSGS] #{room.id}: code={data.get('code', 'N/A')} mess_count={len(data.get('mess', []))}")

                    # Check if API returned an error code (kicked from room)
                    if 'code' in data:
                        error_code = int(data.get('code', 0))
                        # 403 = kicked/banned from room, 404 = room doesn't exist
                        if error_code in (403, 404):
                            error_msg = data.get('message', 'Unknown error')
                            if DEBUG:
                                log(f"[KICKED] Removed from #{room.id}: {error_msg}")
                            self.inst.send_raw(
                                f":{self.inst.user.me} KICK #{room.id} {self.inst.user.nick} :{error_msg}\r\n"
                            )
                            # Remove from rooms list directly (part will fail since we're banned)
                            croom = self.inst.is_in_room(room.id, True)
                            if croom:
                                self.inst.rooms.remove(croom)
                                self.inst.send_raw(f":{self.inst.user.nick} PART #{room.id}\r\n")
                        elif error_code == 401:
                            # Session expired - re-login and retry next poll
                            self.inst.relogin()
                        continue
                except Exception:
                    if DEBUG:
                        tb.print_exc()
                    data = {'mess': []}

                try:
                    for mess in data.get('mess', []):
                        if DEBUG >= 2:
                            log(f"[API MSG] id={mess['id']} typ={mess['typ']} nick={mess['nick']} msg={mess['zprava'][:50]}...")
                        if int(room.last_id) >= int(mess['id']):
                            continue
                        room.last_id = mess['id']
                        room.last_mess = mess['zprava']

                        # Skip messages from ourselves, but NOT system messages (typ 2)
                        # System messages should always be processed (kicks, joins, etc.)
                        if mess["typ"] != 2:
                            if mess['nick'].lower() == self.inst.user.username.lower():
                                continue
                            if mess['nick'].lower() == self.inst.user.nick.lower():
                                continue

                        if room.first_load:
                            continue

                        msg = self.inst.clean_highlight(mess['zprava'])
                        msg = self.inst.clean_smiles(msg)
                        msg = self.inst.clean_urls_mailto(msg)
                        msg = self.inst.clean_urls(msg)

                        if mess["typ"] == 0:  # Public
                            self.inst.send_raw(
                                f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG #{room.id} :{msg}\r\n"
                            )
                        elif mess["typ"] == 1:  # PM
                            self.inst.send_raw(
                                f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG {mess['komu']} :{msg}\r\n"
                            )
                        elif mess["typ"] == 2:  # System
                            if DEBUG:
                                log(f"[SYSTEM MSG] nick={mess['nick']} msg={msg}")
                            self.handle_system_message(mess, msg, room)
                        elif mess["typ"] == 3:  # WALL
                            if self.inst.user.settings_show_pm_from:
                                self.inst.send_raw(
                                    f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG {mess['komu']} :[From room {mess['rname']} #{mess['rid']}] {msg}\r\n"
                                )
                            else:
                                self.inst.send_raw(
                                    f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG {mess['komu']} :{msg}\r\n"
                                )
                        elif mess["typ"] == 10:  # ACTION (/me command)
                            self.inst.send_raw(
                                f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG #{room.id} :\x01ACTION {msg}\x01\r\n"
                            )

                except Exception as e:
                    if DEBUG:
                        tb.print_exc()

                # Idler - use sayAgo from API (server-side idle time)
                say_ago = data.get('sayAgo', {})
                try:
                    say_ago_seconds = int(say_ago.get('min', 0)) * 60 + int(say_ago.get('sec', 0))
                except (ValueError, TypeError):
                    say_ago_seconds = 0
                room.say_ago_seconds = say_ago_seconds  # Store for STATUS command

                if self.inst.user.idler_timer != 0 and self.inst.user.idler_enable:
                    if DEBUG >= 2:
                        log(f"[IDLER] #{room.id}: sayAgo={say_ago_seconds}s, timer={self.inst.user.idler_timer}s")
                    # Send idler message when idle time reached
                    if say_ago_seconds >= self.inst.user.idler_timer:
                        self.inst.send_raw(
                            f":{self.inst.user.me} NOTICE #{room.id} :Idler message sent (idle {say_ago_seconds}s)\r\n"
                        )
                        room.idler_lastsend = time.time()
                        self.inst.send_text(random.choice(self.inst.user.idler_text), room.id, room.id)

            # Away message repeater (every 30 min)
            if self.inst.user.away_message:
                my_time = time.time()
                if (my_time - self.inst.user.away_last_sent) >= self.inst.user.away_interval:
                    for room in self.inst.rooms:
                        self.inst.send_text(self.inst.user.away_message, room.id, room.id)
                    self.inst.user.away_last_sent = my_time
                    self.inst.send_raw(
                        f":{self.inst.user.me} NOTICE {self.inst.user.nick} :Away message repeated to all rooms\r\n"
                    )

            # Load users on first load
            for room in self.inst.rooms:
                if room.first_load:
                    self.inst.reload_users(room.id)
                    room.first_load = False

            # Server-side PING to keep connection alive + PONG timeout check
            if not self.inst.ping_keepalive(time.time()):
                log(f"[TIMEOUT] {self.inst.user.nick}: no PONG received within {self.inst.user.pong_timeout}s, disconnecting")
                self.inst.send_raw(f"ERROR :Closing link: PONG timeout ({self.inst.user.pong_timeout}s)\r\n")
                self.inst.parent.running = False
                self.inst.connection = False
                return

            time.sleep(self.inst.user.timer)

    def handle_system_message(self, mess, msg, room):
        # Security: Limit message length to prevent ReDoS attacks
        if len(msg) > MAX_MESSAGE_LENGTH:
            msg = msg[:MAX_MESSAGE_LENGTH]
        t = msg.replace("'", "")
        u = UserInRoom()

        if "vstoupil" in t or "vstoupila" in t:
            try:
                ret = re.findall(r'.+\s(.+)\svstoupi(la|l)', msg)[0]
                nick = ret[0]
                u.nick = nick
                u.sex = "girls" if ret[1] == "la" else "boys"
                r = self.inst.is_in_room(room.id, True)
                if r:
                    r.users.append(u)
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick, room.id)} JOIN #{room.id}\r\n"
                )
            except Exception:
                if DEBUG:
                    tb.print_exc()

        elif "odešel" in t or "odešla" in t:
            try:
                nick = re.findall(r'.+\s(.+)\s(odešel|odešla)', msg)[0]
                partmess = "Left" if nick[1] == "odešel" else "Left"
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick[0], room.id)} PART #{room.id} :{partmess}\r\n"
                )
            except Exception:
                if DEBUG:
                    tb.print_exc()

        elif "odstraněn" in t:
            try:
                nick = re.findall(r'.+e(lka|l)\s(.+)\sby(la|l)\s', msg)[0]
                nick = nick[1]
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick, room.id)} PART #{room.id} :Inactive\r\n"
                )
            except Exception:
                if DEBUG:
                    tb.print_exc()

        elif "vykopnut" in t:
            try:
                nick = re.findall(
                    r'(lka|l)\s(.+)\sby(la|l)\svykopnu(ta|t)\sz\smístnosti.\sVykop(l|nul)\s(jej|ji)\s(.+)\sz\sdůvodu:\s(.*?)\.$',
                    msg
                )[0]
                target = nick[1]
                duvod = nick[7] if nick[7] else "No reason given"
                kicker = nick[6]
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(kicker, room.id)} KICK #{room.id} {target} :{duvod}\r\n"
                )
                # If the kicked user is us, leave the room
                if target.lower() == self.inst.user.username.lower() or target.lower() == self.inst.user.nick.lower():
                    log(f"Kicked from #{room.id} by {kicker}: {duvod}")
                    self.inst.part(room.id)
            except Exception:
                if DEBUG:
                    tb.print_exc()

        elif "předal" in t or "předala" in t:
            try:
                nick = re.findall(r'.+e(lka|l)\s(.+)\spředa(l|la)\ssprávce\s(.+)', msg)[0]
                target = nick[3]
                nick = nick[1]
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick, room.id)} MODE #{room.id} -h {nick}\r\n"
                )
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick, room.id)} MODE #{room.id} +h {target}\r\n"
                )
                self.inst.reload_users(room.id)
            except Exception:
                if DEBUG:
                    tb.print_exc()

        else:
            # Generic system message as NOTICE
            # Remove timestamp prefix like "19:10:13: " from the beginning
            clean_msg = re.sub(r'^\d{1,2}:\d{2}:\d{2}:\s*', '', msg)
            self.inst.send_raw(
                f":{self.inst.user.me} NOTICE #{room.id} :{clean_msg}\r\n"
            )

class ChatujmeSystem:
    def __init__(self, parent):
        self.url = "https://api.chatujme.cz/irc"  # Security: Always use HTTPS
        self.parent = parent

    def get_rooms(self):
        response = self.parent.get_url(f"{self.url}/get-rooms")
        return json.loads(response)


class Chatujme:
    def __init__(self, sock, address, handler):
        self.socket = sock
        self.address = address
        self.user = User()
        self.system = ChatujmeSystem(self)
        self.connection = True
        self.rooms = []
        self.parent = handler
        self.rfc = IRC_RFC()
        self.cap_negotiating = False
        self.send_lock = threading.Lock()

    def clean_highlight(self, msg):
        return re.sub(r"<span style='background:#eded1a'>([^<]+)</span>", r"\1", msg)

    def clean_urls(self, msg):
        def extract_real_url(match):
            href = match.group(1)
            # Extract real URL from redirect links like //link.chatujme.cz/redirect?url=https%3A%2F%2F...
            if 'link.chatujme.cz/redirect?url=' in href:
                try:
                    # Get the url parameter and decode it
                    url_param = href.split('url=', 1)[1]
                    return urllib.parse.unquote(url_param)
                except Exception:
                    pass
            # Fix protocol-relative URLs
            if href.startswith('//'):
                return 'https:' + href
            return href
        return re.sub(r'<a href="([^"]+)" target="_blank">([^<]+)</a>', extract_real_url, msg)

    def clean_urls_mailto(self, msg):
        return re.sub(r'<a href="mailto:([^"]+)">([^<]+)</a>', r"\1", msg)

    def make_hostmask(self, nick, room_id):
        """Create nick!user@host format with sex info"""
        try:
            room = self.is_in_room(room_id, True)
            if room:
                for u in room.users:
                    if u.nick == nick:
                        return f"{nick}!{nick}@{u.sex}"
            return f"{nick}!{nick}@users"
        except Exception:
            return f"{nick}!{nick}@users"

    def clean_smiles(self, msg):
        if self.user.show_smiles == 0:
            pattern = ""
        elif self.user.show_smiles == 1:
            # Extract smile ID and display as *ID*
            pattern = r"*\2*"
        else:
            pattern = r"\1"
        # Updated regex to handle new API format with aria-label and title attributes
        # Old format: <img src='url' alt='text'>
        # New format: <img src='url' alt='text' aria-label='desc' title='desc'>
        return re.sub(r"<img src='(.+?smiles/([^.]+).gif)' alt='(.+?)'[^>]*>", pattern, msg)

    def is_public_ip(self, ip):
        """Check if IP is public (not localhost or private range)"""
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip)
            return not (addr.is_loopback or addr.is_private or addr.is_reserved)
        except ValueError:
            return False

    def get_url(self, url, retry_count=0):
        """Fetch URL with retry limit to prevent infinite loops"""
        headers = [('User-agent', UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        # Send client IP if it's a public address
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))
        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, timeout=API_TIMEOUT)
            return response.read().decode('utf-8')
        except Exception as e:
            if retry_count >= MAX_RETRIES:
                log(f"[GET_URL] Max retries ({MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.send_raw(f":{self.user.me} NOTICE * :Connection error (retry {retry_count + 1}/{MAX_RETRIES}): {e}\r\n")
            time.sleep(RETRY_DELAY)
            return self.get_url(url, retry_count + 1)

    def post_url(self, url, postdata, retry_count=0):
        """POST URL with retry limit to prevent infinite loops"""
        headers = [('User-agent', UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))
        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, data=postdata.encode('utf-8'), timeout=API_TIMEOUT)
            return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            # Read JSON response from HTTP errors (403, 404, etc.) - these are valid API responses
            if e.code in (400, 403, 404):
                try:
                    return e.read().decode('utf-8')
                except Exception:
                    return f'{{"code": {e.code}, "message": "{e.reason}"}}'
            # Other HTTP errors - retry
            if retry_count >= MAX_RETRIES:
                log(f"[POST_URL] Max retries ({MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.send_raw(f":{self.user.me} NOTICE * :Connection error (retry {retry_count + 1}/{MAX_RETRIES}): {e}\r\n")
            time.sleep(RETRY_DELAY)
            return self.post_url(url, postdata, retry_count + 1)
        except Exception as e:
            if retry_count >= MAX_RETRIES:
                log(f"[POST_URL] Max retries ({MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.send_raw(f":{self.user.me} NOTICE * :Connection error (retry {retry_count + 1}/{MAX_RETRIES}): {e}\r\n")
            time.sleep(RETRY_DELAY)
            return self.post_url(url, postdata, retry_count + 1)

    def reload_users(self, rid):
        data = self.get_room_users(rid)
        users = " ".join([f"{self.user_op_status(u)}{u['nick']}" for u in data])
        self.send(self.rfc.RPL_NAMREPLY, f"= #{rid} :{users}")
        self.send(self.rfc.RPL_ENDOFNAMES, f"#{rid} :End of /NAMES list")

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

    def relogin(self):
        """Re-authenticate after session expiry (silent - no duplicate welcome)."""
        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Session expired, attempting re-login...\r\n")
        self.user.login = self.check_login(silent=True)
        if self.user.login:
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Re-login successful\r\n")
        else:
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Re-login failed, retrying later\r\n")
            time.sleep(10)
        return self.user.login

    def check_login(self, silent=False):
        if not self.user.username or not self.user.nick or not self.user.password:
            if DEBUG:
                log(f"[LOGIN] Missing credentials: user={self.user.username}, nick={self.user.nick}")
            return False

        if DEBUG:
            log(f"[LOGIN] Attempting login for {self.user.username}")

        # Security: Use fresh in-memory cookies for each login (no file storage)
        self.user.cookie_jar = http.cookiejar.CookieJar()
        self.user.url_fetcher = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.user.cookie_jar)
        )

        try:
            # Security: URL-encode username and password to prevent injection
            safe_username = urllib.parse.quote_plus(self.user.username)
            safe_password = urllib.parse.quote_plus(self.user.password)
            postdata = f"username={safe_username}&password={safe_password}"
            response = self.post_url(f"{self.system.url}/check-login", postdata)
            if DEBUG:
                log(f"[LOGIN] API response: {response[:200]}")  # sanitize_log handles password masking
            data = json.loads(response)

            # Security: Safe access to API response fields
            code = data.get('code', 0)
            message = data.get('message', 'Unknown error')

            if code in (200, 201):
                # Ghost mechanism deferred to JOIN (probes would kill real connections)
                if not silent:
                    self.send_welcome()
                log(f"User logged in: {self.user.username}")
                return True
            # 401 = bad credentials, 403 = 2FA required, anything else - relay server message
            self.send(self.rfc.ERR_NOLOGIN, f"{self.user.username} :{message}")
            return False
        except Exception as e:
            log(f"[LOGIN] Error: {e}")
            if DEBUG:
                tb.print_exc()
            return False

    def send_welcome(self):
        """Send proper RFC-compliant welcome sequence"""
        nick = self.user.nick

        # 001 RPL_WELCOME
        self.send(self.rfc.RPL_WELCOME, f":Welcome to Chatujme.cz IRC Gateway {nick}!{nick}@{self.user.me}")
        # 002 RPL_YOURHOST
        self.send(self.rfc.RPL_YOURHOST, f":Your host is {self.user.me}, running ChatujmeGW v{VERSION}")
        # 003 RPL_CREATED
        self.send(self.rfc.RPL_CREATED, ":This server was created for Chatujme.cz")
        # 004 RPL_MYINFO
        self.send(self.rfc.RPL_MYINFO, f"{self.user.me} ChatujmeGW-{VERSION} o o")

        # MOTD
        self.send(self.rfc.RPL_MOTDSTART, f":- {self.user.me} Message of the Day -")
        for line in MOTD_LINES:
            formatted = line.format(user=self.user.username, sex=self.user.sex, host=self.user.me, version=VERSION)
            self.send(self.rfc.RPL_MOTD, f":- {formatted}")
        self.send(self.rfc.RPL_ENDOFMOTD, ":End of /MOTD command")

        # Request client version via CTCP VERSION
        self.send_raw(f":{self.user.me} PRIVMSG {self.user.nick} :\x01VERSION\x01\r\n")

    def is_in_room(self, room, rtn=False):
        try:
            rid = int(room)
        except (ValueError, TypeError):
            return False
        for croom in self.rooms:
            if rid == int(croom.id):
                return croom if rtn else True
        return False

    def join_to_room(self, room_id, key=None):
        url = f"{self.system.url}/join?id={room_id}"

        # Add X-IRC-Client and X-IRC-IP headers
        headers = [('User-agent', UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))

        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, timeout=API_TIMEOUT)
            return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            log(f"[JOIN] Error: {e}")
            return {"code": 500, "message": str(e)}

    def get_room_users(self, room_id):
        response = self.get_url(f"{self.system.url}/get-users?id={room_id}")
        data = json.loads(response)

        r = self.is_in_room(room_id, True)
        if r:
            r.users = []
            for user in data:
                u = UserInRoom()
                u.nick = user["nick"]
                u.sex = user["sex"]
                r.users.append(u)

        return data

    def part(self, room_id, send_to_client=True):
        croom = self.is_in_room(room_id, True)
        if croom:
            self.rooms.remove(croom)
        if send_to_client:
            self.send_raw(f":{self.user.nick} PART #{room_id}\r\n")
        # Always try to notify server about leaving
        try:
            self.get_url_no_retry(f"{self.system.url}/part?id={room_id}")
        except Exception as e:
            if DEBUG:
                log(f"[PART] Error notifying server: {e}")

    def get_url_no_retry(self, url):
        """Get URL without retry on failure - used for disconnect cleanup"""
        headers = [('User-agent', UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))
        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, timeout=5)
            return response.read().decode('utf-8')
        except Exception as e:
            if DEBUG:
                log(f"[GET_NO_RETRY] {url} failed: {e}")
            raise

    def user_op_status(self, user):
        if user.get('isOwner') or user.get('isOP'):
            return "@"
        elif user.get('isHalfOP'):
            return "%"
        elif user.get('sex') == "girls":
            return "+"
        return ""

    def send_text(self, text, room_id, target):
        # Rate limit only for messages to rooms (not internal calls)
        if not self.check_command_rate():
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Rate limit exceeded. Slow down.\r\n")
            return {"code": 429, "message": "Rate limited"}
        postdata = urllib.parse.urlencode({'roomId': room_id, 'text': text, 'target': target})
        response = self.post_url(f"{self.system.url}/post-text", postdata)
        try:
            return json.loads(response)
        except Exception:
            return []

    def check_command_rate(self):
        """Check if command rate limit exceeded. Returns True if allowed."""
        now = time.time()
        # Clean old entries (older than 1 second)
        self.user.command_timestamps = [t for t in self.user.command_timestamps if now - t < 1.0]
        if len(self.user.command_timestamps) >= MAX_COMMANDS_PER_SECOND:
            return False
        self.user.command_timestamps.append(now)
        return True

    def parse(self, data, timestamp):
        if not data:
            self.connection = False
            return 2

        lines = data.replace("\r\n", "\n").split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Security: Limit line length
            if len(line) > MAX_LINE_LENGTH:
                log(f"[SECURITY] Line too long from {self.address} ({len(line)} chars), truncating")
                line = line[:MAX_LINE_LENGTH]

            parts = line.split(" ")
            command = parts[0].upper()

            if DEBUG:
                log(f"<< {sanitize_log(line)}")

            # Rate limit commands that hit the Chatujme API (messages are limited in send_text)
            if command in ("LIST", "WHO", "WHOIS", "NAMES", "TOPIC") and not self.check_command_rate():
                self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Rate limit exceeded. Slow down.\r\n")
                continue

            # CAP handling for modern IRC clients (like girc)
            if command == "CAP":
                self.handle_cap(parts)

            elif command == "NICK":
                if len(parts) < 2:
                    continue
                if self.user.login:
                    self.send_raw(f":{self.user.me} NOTICE {self.user.username} :Already logged in\r\n")
                    continue
                nick = parts[1].lstrip(':')
                # Security: Validate nickname (Chatujme.cz rules)
                if not validate_nick(nick):
                    self.send_raw(f":{self.user.me} NOTICE * :Invalid nickname ({MIN_NICK_LENGTH}-{MAX_NICK_LENGTH} chars, a-z 0-9 - _ only, must start with letter)\r\n")
                    continue
                self.user.nick = nick
                if self.user.password and self.user.username:
                    self.user.login = self.check_login()

            elif command == "USER":
                if len(parts) < 2:
                    continue
                if self.user.login:
                    continue
                self.user.username = parts[1]
                if self.user.password and self.user.nick:
                    self.user.login = self.check_login()

            elif command == "PASS":
                if len(parts) < 2:
                    continue
                if self.user.login:
                    continue
                # Take the whole rest of the line (passwords may contain spaces),
                # strip the optional RFC trailing-parameter colon
                password = line.split(" ", 1)[1]
                if password.startswith(':'):
                    password = password[1:]
                self.user.password = password
                if self.user.username and self.user.nick:
                    self.user.login = self.check_login()

            elif command == "JOIN":
                if len(parts) < 2:
                    continue
                if not self.user.login:
                    self.send(self.rfc.ERR_NOLOGIN, f"{self.user.me} :Not logged in")
                    continue

                rooms = parts[1].replace('#', '').split(',')
                for room in rooms:
                    # Security: Validate room ID before joining
                    if not validate_room_id(room):
                        self.send(self.rfc.ERR_NOSUCHCHANNEL, f"#{room} :Invalid room ID")
                        continue
                    self.handle_join(room)

            elif command == "PART":
                if len(parts) < 2:
                    self.send(self.rfc.ERR_NEEDMOREPARAMS, f"{command} :Not enough parameters")
                else:
                    room_id = parts[1].lstrip('#')
                    if not validate_room_id(room_id):
                        self.send(self.rfc.ERR_NOSUCHCHANNEL, f"#{room_id} :Invalid room ID")
                        continue
                    self.part(room_id)

            elif command == "PING":
                token = parts[1] if len(parts) >= 2 else self.user.me
                self.send_raw(f":{self.user.me} PONG {self.user.me} :{token}\r\n")
                try:
                    self.get_url(f"{self.system.url}/ping")
                except Exception:
                    pass

            elif command == "PONG":
                # Track PONG for timeout detection
                self.user.last_pong_received = time.time()
                self.user.pending_ping_token = None

            elif command == "LIST":
                rooms = self.system.get_rooms()
                self.send(self.rfc.RPL_LISTSTART, "Channel :Users Name")
                for room in rooms:
                    self.send(self.rfc.RPL_LIST, f"#{room['id']} {room['online']} :{room['nazev']}")
                self.send(self.rfc.RPL_LISTEND, ":End of /LIST")

            elif command == "MODE":
                if len(parts) >= 2:
                    target = parts[1]
                    # MODE #room +o nick - give operator to nick (predej spravce)
                    if len(parts) >= 4 and parts[2] in ('+o', '+O'):
                        room_id = target.lstrip('#')
                        nick = parts[3]
                        self.send_text(f"/predej {nick}", room_id, room_id)
                        self.send_raw(f":{self.user.me} MODE #{room_id} +o {nick}\r\n")
                    # MODE #room +b - list bans (not supported, return empty)
                    elif len(parts) >= 3 and parts[2] == '+b':
                        room_id = target.lstrip('#')
                        self.send_raw(f":{self.user.me} 368 {self.user.nick} #{room_id} :End of channel ban list\r\n")
                    else:
                        # Just return channel modes
                        self.send(self.rfc.RPL_CHANNELMODEIS, f"{target} +tn")

            elif command == "WHO":
                if len(parts) >= 2:
                    room_id = parts[1].lstrip('#')
                    if room_id.isdigit():
                        users = self.get_room_users(room_id)
                        for user in users:
                            self.send(
                                self.rfc.RPL_WHOREPLY,
                                f"#{room_id} {user['nick']} {user['sex']} {self.user.me} {user['nick']} H :0 {user['nick']}"
                            )
                    self.send(self.rfc.RPL_ENDOFWHO, ":End of /WHO list")

            elif command == "NAMES":
                if len(parts) >= 2:
                    room_id = parts[1].lstrip('#')
                    if self.is_in_room(room_id):
                        self.reload_users(room_id)
                    else:
                        self.send(self.rfc.ERR_NOSUCHCHANNEL, f"#{room_id} :No such channel")

            elif command == "PRIVMSG" or command == "NOTICE":
                if len(parts) < 3:
                    continue
                if not self.user.login:
                    self.send(self.rfc.ERR_NOLOGIN, ":You have not registered")
                    continue
                target = parts[1]
                # Get message after the colon
                msg_start = line.find(':', 1)
                if msg_start == -1:
                    text = ' '.join(parts[2:])
                else:
                    text = line[msg_start + 1:]

                # Security: Sanitize message content (prevent CRLF injection)
                text = sanitize_irc(text)
                target = sanitize_irc(target)

                # Security: Limit message length
                if len(text) > MAX_MESSAGE_LENGTH:
                    text = text[:MAX_MESSAGE_LENGTH]
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Message truncated to {MAX_MESSAGE_LENGTH} characters\r\n")

                # Handle CTCP requests/replies (wrapped in \x01)
                if text.startswith('\x01') and text.endswith('\x01'):
                    ctcp_content = text.strip('\x01')
                    ctcp_parts = ctcp_content.split(' ', 1)
                    ctcp_cmd = ctcp_parts[0].upper()

                    # NOTICE with CTCP = reply from client
                    if command == "NOTICE":
                        if ctcp_cmd == "VERSION" and len(ctcp_parts) > 1:
                            # Client sent VERSION reply - store it
                            self.user.client_version = ctcp_parts[1]
                            log(f"Client version for {self.user.nick}: {self.user.client_version}")
                        continue

                    # PRIVMSG with CTCP = request to server
                    if ctcp_cmd == "VERSION":
                        # Reply with CTCP VERSION response
                        version_reply = f"ChatujmeGW {VERSION} - Python {sys.version.split()[0]} on {sys.platform}"
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :\x01VERSION {version_reply}\x01\r\n")
                        continue
                    elif ctcp_cmd == "PING":
                        # Echo back PING for latency measurement
                        ping_data = text.strip('\x01')[5:].strip()  # Get data after "PING "
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :\x01PING {ping_data}\x01\r\n")
                        continue
                    elif ctcp_cmd == "TIME":
                        # Reply with current time
                        time_str = time.strftime("%a %b %d %H:%M:%S %Y")
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :\x01TIME {time_str}\x01\r\n")
                        continue
                    elif ctcp_cmd == "ACTION":
                        # Handle /me command - convert to Chatujme.cz /me format
                        action_text = text.strip('\x01')[7:]  # Get text after "ACTION "
                        if action_text and target.startswith('#'):
                            room_id = target.lstrip('#')
                            room = self.is_in_room(room_id, True)
                            if room:
                                room.idler_lastsend = time.time()
                                self.send_text(f"/me {action_text}", room_id, target)
                                # Auto-disable away
                                if self.user.away_message:
                                    self.user.away_message = None
                                    self.user.away_last_sent = 0
                                    self.send(self.rfc.RPL_UNAWAY, ":You are no longer marked as being away")
                        continue
                    # Other CTCP commands are ignored

                is_pm = not target.startswith('#')

                # Handle NickServ REGISTER command
                if target.lower() == "nickserv" and text.lower().startswith("register"):
                    self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Registration is only available via web.\r\n")
                    self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Please visit: https://chatujme.cz/registrace\r\n")
                    continue

                if is_pm:
                    if not self.rooms:
                        self.send(self.rfc.ERR_NOSUCHNICK, f"{target} :Cannot send PM - join a room first")
                        continue
                    text = f"/m {target} {text}"
                    room_id = self.rooms[0].id
                    self.rooms[0].idler_lastsend = time.time()
                else:
                    room_id = target.lstrip('#')
                    room = self.is_in_room(room_id, True)
                    if room:
                        room.idler_lastsend = time.time()

                # Auto-disable away when user sends a message
                if self.user.away_message:
                    self.user.away_message = None
                    self.user.away_last_sent = 0
                    self.send(self.rfc.RPL_UNAWAY, ":You are no longer marked as being away")
                    for room in self.rooms:
                        self.send_raw(f":{self.user.nick}!{self.user.nick}@{self.user.me} AWAY\r\n")

                self.send_text(text, room_id, target)

            elif command == "KICK":
                if len(parts) >= 3:
                    room_id = parts[1].lstrip('#')
                    nick = parts[2]
                    reason = ' '.join(parts[3:]).lstrip(':') if len(parts) > 3 else ""
                    self.send_text(f"/kick {nick} {reason}".strip(), room_id, room_id)
                    self.send_raw(
                        f":{self.make_hostmask(self.user.username, room_id)} KICK #{room_id} {nick} :{reason}\r\n"
                    )
                else:
                    self.send(self.rfc.ERR_NEEDMOREPARAMS, "KICK :Not enough parameters")

            elif command == "TOPIC":
                if len(parts) >= 2:
                    room_id = parts[1].lstrip('#')
                    # Check if setting new topic or just viewing
                    if len(parts) >= 3:
                        # Setting new topic: TOPIC #room :new topic text
                        new_topic = ' '.join(parts[2:]).lstrip(':')
                        if new_topic:
                            try:
                                # Call set-topic API endpoint
                                postdata = urllib.parse.urlencode({
                                    'roomId': room_id,
                                    'topic': new_topic
                                })
                                response = self.post_url(f"{self.system.url}/set-topic", postdata)
                                data = json.loads(response)
                                if data.get('code') == 200:
                                    # Success - send topic change notification
                                    self.send_raw(
                                        f":{self.make_hostmask(self.user.username, room_id)} TOPIC #{room_id} :{new_topic}\r\n"
                                    )
                                elif data.get('code') == 403:
                                    # No permission
                                    self.send_raw(f":{self.user.me} 482 {self.user.nick} #{room_id} :{data.get('message', 'Permission denied')}\r\n")
                                else:
                                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Error: {data.get('message', 'Unknown error')}\r\n")
                            except Exception as e:
                                if DEBUG:
                                    tb.print_exc()
                                self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Error changing topic: {e}\r\n")
                        else:
                            # Empty topic = show current
                            try:
                                response = self.get_url(f"{self.system.url}/get-room?id={int(room_id)}")
                                data = json.loads(response)
                                self.send(self.rfc.RPL_TOPIC, f"#{data['id']} :[{data['nazev']}] {data['topic']}")
                            except Exception:
                                if DEBUG:
                                    tb.print_exc()
                    else:
                        # Just viewing topic
                        try:
                            response = self.get_url(f"{self.system.url}/get-room?id={int(room_id)}")
                            data = json.loads(response)
                            self.send(self.rfc.RPL_TOPIC, f"#{data['id']} :[{data['nazev']}] {data['topic']}")
                        except Exception:
                            if DEBUG:
                                tb.print_exc()

            elif command == "WHOIS":
                if len(parts) >= 2:
                    nick = parts[1].lstrip(':')
                    self.handle_whois(nick)

            elif command == "USERHOST":
                if len(parts) >= 2:
                    nick = parts[1]
                    self.send(self.rfc.RPL_USERHOST, f"{nick}=+~{nick}@{self.user.me}")

            elif command == "VERSION":
                # RPL_VERSION (351): <version>.<debuglevel> <server> :<comments>
                self.send(self.rfc.RPL_VERSION, f"ChatujmeGW-{VERSION}.{DEBUG} {self.user.me} :Python {sys.version.split()[0]} on {sys.platform}")

            elif command == "MOTD":
                # Send MOTD on request
                self.send(self.rfc.RPL_MOTDSTART, f":- {self.user.me} Message of the Day -")
                for line in MOTD_LINES:
                    formatted = line.format(user=self.user.username, sex=self.user.sex, host=self.user.me, version=VERSION)
                    self.send(self.rfc.RPL_MOTD, f":- {formatted}")
                self.send(self.rfc.RPL_ENDOFMOTD, ":End of /MOTD command")

            elif command == "AWAY":
                # AWAY [message] - set/unset away status with auto-message to rooms
                if len(parts) > 1:
                    # Set away with message
                    msg_start = line.find(':', 1)
                    if msg_start != -1:
                        away_msg = line[msg_start + 1:]
                    else:
                        away_msg = ' '.join(parts[1:])
                    self.user.away_message = away_msg
                    self.user.away_last_sent = time.time()
                    self.send(self.rfc.RPL_NOWAWAY, ":You have been marked as being away")
                    # Notify all rooms (away-notify capability)
                    for room in self.rooms:
                        self.send_raw(f":{self.user.nick}!{self.user.nick}@{self.user.me} AWAY :{away_msg}\r\n")
                    # Send away message to all rooms immediately
                    for room in self.rooms:
                        self.send_text(away_msg, room.id, room.id)
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Away message sent to all rooms (will repeat every 30 min)\r\n")
                else:
                    # Unset away
                    self.user.away_message = None
                    self.user.away_last_sent = 0
                    self.send(self.rfc.RPL_UNAWAY, ":You are no longer marked as being away")
                    # Notify all rooms (away-notify capability)
                    for room in self.rooms:
                        self.send_raw(f":{self.user.nick}!{self.user.nick}@{self.user.me} AWAY\r\n")

            elif command == "QUIT":
                # Leave all rooms - don't send PART back to client (they're quitting)
                for room in self.rooms[:]:
                    self.part(room.id, send_to_client=False)
                self.parent.running = False
                self.connection = False

            elif command == "REGISTER":
                # Direct REGISTER command - redirect to web
                self.send_raw(f":{self.user.me} NOTICE * :Registration is only available via web.\r\n")
                self.send_raw(f":{self.user.me} NOTICE * :Please visit: https://chatujme.cz/registrace\r\n")

            elif command == "NICKSERV" or command == "NS":
                # Handle NICKSERV/NS shortcut commands (some clients send these)
                if len(parts) > 1:
                    subcmd = parts[1].lower()
                    if subcmd == "identify" or subcmd == "id":
                        # Already logged in via PASS, ignore
                        self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :You are already identified.\r\n")
                    elif subcmd == "register":
                        self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Registration is only available via web.\r\n")
                        self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Please visit: https://chatujme.cz/registrace\r\n")
                    else:
                        self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Unknown NickServ command: {subcmd}\r\n")

            elif command == "IDLER":
                # IDLER [ON|OFF|STATUS|TIME <seconds>|TEXT <text>]
                # Auto-send message when idle for specified time
                if len(parts) < 2:
                    # Show help
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :IDLER - Auto-send message when idle\r\n")
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :  IDLER ON        - Enable idler\r\n")
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :  IDLER OFF       - Disable idler\r\n")
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :  IDLER STATUS    - Show current settings\r\n")
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :  IDLER TIME <s>  - Set idle time in seconds (default: 2400 = 40min)\r\n")
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :  IDLER TEXT <t>  - Set idler message(s), comma-separated\r\n")
                else:
                    subcmd = parts[1].upper()
                    if subcmd == "ON":
                        self.user.idler_enable = True
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Idler enabled (time: {self.user.idler_timer}s)\r\n")
                    elif subcmd == "OFF":
                        self.user.idler_enable = False
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Idler disabled\r\n")
                    elif subcmd == "STATUS":
                        status = "ON" if self.user.idler_enable else "OFF"
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Idler: {status}, Time: {self.user.idler_timer}s ({self.user.idler_timer//60}min), Text: {self.user.idler_text}\r\n")
                        # Show per-channel idler status using server-side sayAgo
                        if self.rooms:
                            for room in self.rooms:
                                idle = room.say_ago_seconds
                                remaining = max(0, self.user.idler_timer - idle)
                                self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :  #{room.id}: idle {idle}s, {remaining}s remaining ({remaining//60}min {remaining%60}s)\r\n")
                    elif subcmd == "TIME" and len(parts) >= 3:
                        try:
                            new_time = int(parts[2])
                            if new_time < 1800:  # 30 minutes minimum
                                self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Minimum idler time is 1800 seconds (30 minutes)\r\n")
                            else:
                                self.user.idler_timer = new_time
                                self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Idler time set to {new_time}s ({new_time//60}min)\r\n")
                        except ValueError:
                            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Invalid time value\r\n")
                    elif subcmd == "TEXT" and len(parts) >= 3:
                        text = ' '.join(parts[2:])
                        self.user.idler_text = [t.strip() for t in text.split(',')]
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Idler text set to: {self.user.idler_text}\r\n")
                    else:
                        self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Unknown IDLER subcommand. Use: ON, OFF, STATUS, TIME, TEXT\r\n")

            elif command not in ("", "CAP"):
                self.send(self.rfc.ERR_UNKNOWNCOMMAND, f"{command} :Unknown command")

    # Supported IRC capabilities
    SUPPORTED_CAPS = ["away-notify"]

    def handle_cap(self, parts):
        """Handle CAP negotiation for modern IRC clients"""
        if len(parts) < 2:
            return

        subcmd = parts[1].upper()
        if subcmd == "LS":
            # Send supported capabilities
            caps_str = ' '.join(self.SUPPORTED_CAPS)
            self.send_raw(f":{self.user.me} CAP * LS :{caps_str}\r\n")
            self.cap_negotiating = True
        elif subcmd == "LIST":
            # List enabled capabilities
            caps_str = ' '.join(self.SUPPORTED_CAPS)
            self.send_raw(f":{self.user.me} CAP * LIST :{caps_str}\r\n")
        elif subcmd == "REQ":
            # Handle capability requests
            caps = ' '.join(parts[2:]).lstrip(':') if len(parts) > 2 else ""
            requested = caps.split()
            # Check if all requested caps are supported
            all_supported = all(cap.lstrip('-') in self.SUPPORTED_CAPS for cap in requested)
            if all_supported:
                self.send_raw(f":{self.user.me} CAP * ACK :{caps}\r\n")
            else:
                self.send_raw(f":{self.user.me} CAP * NAK :{caps}\r\n")
        elif subcmd == "END":
            self.cap_negotiating = False
            # If we have credentials and not already logged in, try to login
            if not self.user.login and self.user.password and self.user.nick and self.user.username:
                self.user.login = self.check_login()

    def handle_whois(self, nick):
        """Handle WHOIS command - gather info about user from all joined rooms"""
        found = False
        user_sex = "users"
        user_rooms = []
        user_status = {}  # room_id -> status (op/halfop/owner)

        # Search in all joined rooms
        for room in self.rooms:
            for u in room.users:
                if u.nick.lower() == nick.lower():
                    found = True
                    user_sex = u.sex
                    # Get status from API
                    try:
                        users_data = self.get_room_users(room.id)
                        for ud in users_data:
                            if ud['nick'].lower() == nick.lower():
                                status = ""
                                if ud.get('isOwner'):
                                    status = "@"  # Owner
                                elif ud.get('isOP'):
                                    status = "@"  # OP
                                elif ud.get('isHalfOP'):
                                    status = "%"  # HalfOP
                                elif ud.get('sex') == "girls":
                                    status = "+"  # Voice for girls
                                user_rooms.append(f"{status}#{room.id}")
                                user_status[room.id] = {
                                    'isOwner': ud.get('isOwner', False),
                                    'isOP': ud.get('isOP', False),
                                    'isHalfOP': ud.get('isHalfOP', False)
                                }
                                break
                    except Exception:
                        user_rooms.append(f"#{room.id}")

        if not found:
            # User not in any of our rooms - send basic info
            self.send(self.rfc.ERR_NOSUCHNICK, f"{nick} :No such nick/channel")
            return

        # Build realname based on sex
        realname = "Male user" if user_sex == "boys" else "Female user" if user_sex == "girls" else "User"

        # 311 RPL_WHOISUSER: <nick> <user> <host> * :<realname>
        self.send(self.rfc.RPL_WHOISUSER, f"{nick} ~{nick} {user_sex}.chatujme.cz * :{realname}")

        # 312 RPL_WHOISSERVER: <nick> <server> :<server info>
        self.send(self.rfc.RPL_WHOISSERVER, f"{nick} {self.user.me} :Chatujme.cz IRC Gateway")

        # 319 RPL_WHOISCHANNELS: <nick> :<channels>
        if user_rooms:
            self.send(self.rfc.RPL_WHOISCHANNELS, f"{nick} :{' '.join(user_rooms)}")

        # 378 RPL_WHOISHOST: <nick> :is connecting from <host>
        self.send(self.rfc.RPL_WHOISHOST, f"{nick} :is connecting from {user_sex}.chatujme.cz")

        # Check if user has any special status
        has_op = any(s.get('isOP') or s.get('isOwner') for s in user_status.values())
        if has_op:
            # 313 RPL_WHOISOPERATOR (custom usage)
            self.send_raw(f":{self.user.me} 313 {self.user.nick} {nick} :is a room operator\r\n")

        # 318 RPL_ENDOFWHOIS
        self.send(self.rfc.RPL_ENDOFWHOIS, f"{nick} :End of /WHOIS list")

    def handle_join(self, room):
        in_room = self.is_in_room(room)
        data = self.join_to_room(room)

        if DEBUG:
            log(f"JOIN to {room}: {data}")

        # Security: Safe access to API response
        code = data.get('code', 0)

        if code == 403:
            # Banned from channel - send both RFC error and NOTICE with detailed message
            ban_msg = data.get('message', 'Cannot join channel')
            self.send(self.rfc.ERR_BANNEDFROMCHAN, f"#{data.get('id', room)} :Cannot join channel (+b)")
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :{ban_msg}\r\n")
        elif code == 404:
            self.send(self.rfc.ERR_NOSUCHCHANNEL, f"#{room} :No such channel")
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :{data.get('message', 'Room does not exist')}\r\n")
        elif code != 200:
            # Unknown error code - show as notice
            err_msg = data.get('message', f'Unknown error (code {code})')
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Error joining #{room}: {err_msg}\r\n")
        else:
            # Ghost: activate connection and kill duplicates on first real JOIN
            nick_lower = self.user.nick.lower()
            with active_connections_lock:
                old_handler = active_connections.get(nick_lower)
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
                        # Old cleanup must not part rooms the new session is (re)joining
                        old_handler.instance.rooms = []
                    try:
                        # Close the socket so the old thread's blocking recv exits now,
                        # not after the 300s timeout
                        old_handler.socket.close()
                    except Exception:
                        pass
                active_connections[nick_lower] = self.parent

            users_data = self.get_room_users(room)
            users = " ".join([f"{self.user_op_status(u)}{u['nick']}" for u in users_data])

            if not in_room:
                nowroom = RoomStruct()
                nowroom.id = int(data['id'])
                nowroom.nick = self.user.username
                nowroom.idler_lastsend = time.time()
                self.rooms.append(nowroom)

            # Send JOIN confirmation
            self.send_raw(f":{self.user.nick}!{self.user.nick}@{self.user.me} JOIN #{data['id']}\r\n")
            self.send(self.rfc.RPL_TOPIC, f"#{data['id']} :[{data['nazev']}] {data['topic']}")
            self.send(self.rfc.RPL_NAMREPLY, f"= #{data['id']} :{users}")
            self.send(self.rfc.RPL_ENDOFNAMES, f"#{data['id']} :End of /NAMES list")

    def send(self, code, msg):
        """Send IRC numeric reply"""
        line = f":{self.user.me} {code} {self.user.nick} {msg}\r\n"
        self.send_raw(line)

    def send_raw(self, msg):
        """Send raw IRC message with length limit"""
        # Security: Limit message length to prevent buffer issues
        if len(msg) > MAX_LINE_LENGTH + 2:  # +2 for \r\n
            msg = msg[:MAX_LINE_LENGTH] + "\r\n"
        if DEBUG:
            log(f">> {msg.strip()}")
        try:
            # Lock + sendall: two threads (parse + GetMessages) share this socket;
            # a bare send() may write partially and interleave lines
            with self.send_lock:
                self.socket.sendall(msg.encode('utf-8'))
        except Exception as e:
            if DEBUG:
                log(f"Send error: {e}")


class SocketHandler(threading.Thread):
    PROBE_THRESHOLD = 5  # Connections shorter than 5s are considered probes

    def __init__(self, sock, address):
        threading.Thread.__init__(self)
        self.socket = sock
        self.address = address
        self.running = True
        self.daemon = True
        self.instance = None  # Set in run(), used by ghost mechanism
        self.connect_time = time.time()  # Track connection start for probe detection
        self.recv_buffer = ""  # Security: Buffer for incomplete data

    def run(self):
        log(f"Connection accepted from {self.address[0]}")
        instance = Chatujme(self.socket, self.address[0], self)
        self.instance = instance  # Store for ghost mechanism
        instance.send_raw(f":{instance.user.me} NOTICE * :Connected from {self.address[0]}, waiting for login.\r\n")

        while self.running:
            timestamp = int(time.time())
            try:
                # Security: Read in smaller chunks
                chunk = instance.socket.recv(1024).decode('utf-8', errors='replace')
                if not chunk:
                    # Connection closed
                    break

                self.recv_buffer += chunk

                # Security: Check buffer size limit
                if len(self.recv_buffer) > MAX_BUFFER_SIZE:
                    log(f"[SECURITY] Buffer overflow attempt from {self.address[0]}, disconnecting")
                    instance.send_raw(f":{instance.user.me} ERROR :Buffer overflow - disconnecting\r\n")
                    break

                # Process complete lines only
                while '\r\n' in self.recv_buffer or '\n' in self.recv_buffer:
                    # Find line terminator
                    rn_pos = self.recv_buffer.find('\r\n')
                    n_pos = self.recv_buffer.find('\n')

                    if rn_pos != -1 and (n_pos == -1 or rn_pos < n_pos):
                        line = self.recv_buffer[:rn_pos]
                        self.recv_buffer = self.recv_buffer[rn_pos + 2:]
                    elif n_pos != -1:
                        line = self.recv_buffer[:n_pos]
                        self.recv_buffer = self.recv_buffer[n_pos + 1:]
                    else:
                        break

                    # Parse complete line
                    result = instance.parse(line + "\r\n", timestamp)
                    if DEBUG:
                        log(f"[PARSE] result={result}, login={instance.user.login}, nick={instance.user.nick}")
                    if result == 2:
                        if DEBUG:
                            log("[PARSE] Breaking due to result=2")
                        self.running = False
                        break

            except socket.timeout:
                # Normal timeout, continue
                continue
            except Exception as e:
                log(f"Connection from {self.address[0]} closed: {e}")
                if DEBUG:
                    tb.print_exc()
                # Leave all rooms on disconnect - don't send to client (already disconnected)
                for room in instance.rooms[:]:
                    instance.part(room.id, send_to_client=False)
                instance.connection = False
                break

            if instance.user.nick and instance.user.login and not instance.user.reading:
                try:
                    with thread_lock:
                        World.vlakna.append(GetMessages(instance, self.socket))
                    World.collector.start_threads()
                    instance.user.reading = True
                except Exception as e:
                    if DEBUG:
                        tb.print_exc()
                    break

        # Cleanup on disconnect
        for room in instance.rooms[:]:
            instance.part(room.id, send_to_client=False)
        instance.connection = False

        # Remove from active connections registry
        if instance.user.nick:
            nick_lower = instance.user.nick.lower()
            with active_connections_lock:
                if active_connections.get(nick_lower) is self:
                    del active_connections[nick_lower]

        # Probe-friendly rate limiting: short-lived connections (probes, health checks)
        # don't count against the rate limit budget
        connection_duration = time.time() - self.connect_time
        if connection_duration < self.PROBE_THRESHOLD:
            uncount_rate_limit(self.address[0])

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
    ssl_required = SSL_ENABLED or DUAL_PORT_MODE

    if ssl_required:
        if not SSL_CERT or not SSL_KEY:
            log("ERROR: SSL enabled but --ssl-cert and --ssl-key are required")
            fatal_error_pause()
            sys.exit(1)

        if not os.path.exists(SSL_CERT):
            log(f"ERROR: SSL certificate not found: {SSL_CERT}")
            fatal_error_pause()
            sys.exit(1)

        if not os.path.exists(SSL_KEY):
            log(f"ERROR: SSL key not found: {SSL_KEY}")
            fatal_error_pause()
            sys.exit(1)

        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2  # Security: Disable old protocols
            ssl_context.load_cert_chain(SSL_CERT, SSL_KEY)
            log(f"SSL/TLS enabled with certificate: {SSL_CERT}")
        except ssl.SSLError as e:
            log(f"ERROR: Failed to load SSL certificate: {e}")
            fatal_error_pause()
            sys.exit(1)

    # Create server sockets
    server_sockets = []
    socket_info = {}  # Maps socket to (port, use_ssl)

    if DUAL_PORT_MODE:
        # Dual-port mode: plain on PORT, SSL on SSL_PORT
        s_plain = create_server_socket(BIND, PORT, " (plain)")
        if not s_plain:
            fatal_error_pause()
            sys.exit(1)
        server_sockets.append(s_plain)
        socket_info[s_plain] = (PORT, False)

        s_ssl = create_server_socket(BIND, SSL_PORT, " (SSL)")
        if not s_ssl:
            s_plain.close()
            fatal_error_pause()
            sys.exit(1)
        server_sockets.append(s_ssl)
        socket_info[s_ssl] = (SSL_PORT, True)

        log(f"ChatujmeGW {VERSION} (Python 3), dual-port mode:")
        log(f"  Plain IRC on {BIND}:{PORT}")
        log(f"  SSL/TLS IRC on {BIND}:{SSL_PORT}")
    else:
        # Single port mode
        s = create_server_socket(BIND, PORT)
        if not s:
            fatal_error_pause()
            sys.exit(1)
        server_sockets.append(s)
        socket_info[s] = (PORT, SSL_ENABLED)

        ssl_status = " [SSL/TLS]" if SSL_ENABLED else ""
        log(f"ChatujmeGW {VERSION} (Python 3), listening on {BIND}:{PORT}{ssl_status}")

    World.collector = Collector()
    World.collector.start()

    try:
        while World.collector.running:
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
                    if not check_rate_limit(address[0]):
                        log(f"[SECURITY] Rate limit exceeded for {address[0]}, rejecting connection")
                        try:
                            connection.send(b"ERROR :Too many connections from your IP. Try again later.\r\n")
                        except Exception:
                            pass
                        connection.close()
                        continue

                    # Wrap connection with SSL if this socket requires it
                    if use_ssl and ssl_context:
                        try:
                            connection = ssl_context.wrap_socket(connection, server_side=True)
                            if DEBUG:
                                log(f"[SSL] Secure connection established with {address[0]}:{port}")
                        except ssl.SSLError as e:
                            log(f"[SSL] Handshake failed for {address[0]}: {e}")
                            try:
                                connection.close()
                            except Exception:
                                pass
                            continue

                    connection.settimeout(300)  # 5 min timeout for client connections
                    with thread_lock:
                        # Security: Max connections limit (count only client handlers,
                        # World.vlakna also holds one GetMessages thread per logged-in user)
                        client_count = sum(1 for t in World.vlakna if isinstance(t, SocketHandler))
                        if client_count < MAX_CLIENTS:
                            handler = SocketHandler(connection, address)
                            World.vlakna.append(handler)
                        else:
                            log(f"[SECURITY] Max connections reached, rejecting {address[0]}")
                            try:
                                connection.send(b"ERROR :Server is full. Try again later.\r\n")
                            except Exception:
                                pass
                            connection.close()
                            continue
                    World.collector.start_threads()

            except Exception as e:
                if DEBUG:
                    log(f"Accept error: {e}")
    except KeyboardInterrupt:
        log("Received shutdown signal...")
    finally:
        World.collector.running = False
        for s in server_sockets:
            try:
                s.close()
            except Exception:
                pass
        # Wait for threads to finish (graceful shutdown)
        shutdown_timeout = 5
        start_time = time.time()
        while World.vlakna and (time.time() - start_time) < shutdown_timeout:
            time.sleep(0.1)
        log("Shutting down...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        if DEBUG:
            tb.print_exc()
        fatal_error_pause()
        sys.exit(1)
