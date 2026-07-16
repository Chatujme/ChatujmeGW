"""IRC session core: protocol parsing, Chatujme API calls, message translation."""

import http.cookiejar
import json
import re
import sys
import threading
import time
import traceback as tb
import urllib.error
import urllib.parse
import urllib.request

from . import config, state
from .models import User, UserInRoom, RoomStruct
from .util import log, sanitize_irc, sanitize_log, validate_nick, validate_room_id


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
        headers = [('User-agent', config.UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        # Send client IP if it's a public address
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))
        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, timeout=config.API_TIMEOUT)
            return response.read().decode('utf-8')
        except Exception as e:
            if retry_count >= config.MAX_RETRIES:
                log(f"[GET_URL] Max retries ({config.MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.send_raw(f":{self.user.me} NOTICE * :Connection error (retry {retry_count + 1}/{config.MAX_RETRIES}): {e}\r\n")
            time.sleep(config.RETRY_DELAY)
            return self.get_url(url, retry_count + 1)

    def post_url(self, url, postdata, retry_count=0):
        """POST URL with retry limit to prevent infinite loops"""
        headers = [('User-agent', config.UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))
        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, data=postdata.encode('utf-8'), timeout=config.API_TIMEOUT)
            return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            # Read JSON response from HTTP errors (403, 404, etc.) - these are valid API responses
            if e.code in (400, 403, 404):
                try:
                    return e.read().decode('utf-8')
                except Exception:
                    return f'{{"code": {e.code}, "message": "{e.reason}"}}'
            # Other HTTP errors - retry
            if retry_count >= config.MAX_RETRIES:
                log(f"[POST_URL] Max retries ({config.MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.send_raw(f":{self.user.me} NOTICE * :Connection error (retry {retry_count + 1}/{config.MAX_RETRIES}): {e}\r\n")
            time.sleep(config.RETRY_DELAY)
            return self.post_url(url, postdata, retry_count + 1)
        except Exception as e:
            if retry_count >= config.MAX_RETRIES:
                log(f"[POST_URL] Max retries ({config.MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.send_raw(f":{self.user.me} NOTICE * :Connection error (retry {retry_count + 1}/{config.MAX_RETRIES}): {e}\r\n")
            time.sleep(config.RETRY_DELAY)
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
            if config.DEBUG:
                log(f"[LOGIN] Missing credentials: user={self.user.username}, nick={self.user.nick}")
            return False

        if config.DEBUG:
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
            if config.DEBUG:
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
            if config.DEBUG:
                tb.print_exc()
            return False

    def send_welcome(self):
        """Send proper RFC-compliant welcome sequence"""
        nick = self.user.nick

        # 001 RPL_WELCOME
        self.send(self.rfc.RPL_WELCOME, f":Welcome to Chatujme.cz IRC Gateway {nick}!{nick}@{self.user.me}")
        # 002 RPL_YOURHOST
        self.send(self.rfc.RPL_YOURHOST, f":Your host is {self.user.me}, running ChatujmeGW v{config.VERSION}")
        # 003 RPL_CREATED
        self.send(self.rfc.RPL_CREATED, ":This server was created for Chatujme.cz")
        # 004 RPL_MYINFO
        self.send(self.rfc.RPL_MYINFO, f"{self.user.me} ChatujmeGW-{config.VERSION} o o")

        # MOTD
        self.send(self.rfc.RPL_MOTDSTART, f":- {self.user.me} Message of the Day -")
        for line in MOTD_LINES:
            formatted = line.format(user=self.user.username, sex=self.user.sex, host=self.user.me, version=config.VERSION)
            self.send(self.rfc.RPL_MOTD, f":- {formatted}")
        self.send(self.rfc.RPL_ENDOFMOTD, ":End of /MOTD command")

        # Request client version via CTCP config.VERSION
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
        headers = [('User-agent', config.UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))

        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, timeout=config.API_TIMEOUT)
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
            if config.DEBUG:
                log(f"[PART] Error notifying server: {e}")

    def get_url_no_retry(self, url):
        """Get URL without retry on failure - used for disconnect cleanup"""
        headers = [('User-agent', config.UA)]
        if self.user.client_version:
            headers.append(('X-IRC-Client', self.user.client_version))
        if self.is_public_ip(self.address):
            headers.append(('X-IRC-IP', self.address))
        self.user.url_fetcher.addheaders = headers
        try:
            response = self.user.url_fetcher.open(url, timeout=5)
            return response.read().decode('utf-8')
        except Exception as e:
            if config.DEBUG:
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
        if len(self.user.command_timestamps) >= config.MAX_COMMANDS_PER_SECOND:
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
            if len(line) > config.MAX_LINE_LENGTH:
                log(f"[SECURITY] Line too long from {self.address} ({len(line)} chars), truncating")
                line = line[:config.MAX_LINE_LENGTH]

            parts = line.split(" ")
            command = parts[0].upper()

            if config.DEBUG:
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
                    self.send_raw(f":{self.user.me} NOTICE * :Invalid nickname ({config.MIN_NICK_LENGTH}-{config.MAX_NICK_LENGTH} chars, a-z 0-9 - _ only, must start with letter)\r\n")
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
                if len(text) > config.MAX_MESSAGE_LENGTH:
                    text = text[:config.MAX_MESSAGE_LENGTH]
                    self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Message truncated to {config.MAX_MESSAGE_LENGTH} characters\r\n")

                # Handle CTCP requests/replies (wrapped in \x01)
                if text.startswith('\x01') and text.endswith('\x01'):
                    ctcp_content = text.strip('\x01')
                    ctcp_parts = ctcp_content.split(' ', 1)
                    ctcp_cmd = ctcp_parts[0].upper()

                    # NOTICE with CTCP = reply from client
                    if command == "NOTICE":
                        if ctcp_cmd == "VERSION" and len(ctcp_parts) > 1:
                            # Client sent config.VERSION reply - store it
                            self.user.client_version = ctcp_parts[1]
                            log(f"Client version for {self.user.nick}: {self.user.client_version}")
                        continue

                    # PRIVMSG with CTCP = request to server
                    if ctcp_cmd == "VERSION":
                        # Reply with CTCP config.VERSION response
                        version_reply = f"ChatujmeGW {config.VERSION} - Python {sys.version.split()[0]} on {sys.platform}"
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
                                if config.DEBUG:
                                    tb.print_exc()
                                self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Error changing topic: {e}\r\n")
                        else:
                            # Empty topic = show current
                            try:
                                response = self.get_url(f"{self.system.url}/get-room?id={int(room_id)}")
                                data = json.loads(response)
                                self.send(self.rfc.RPL_TOPIC, f"#{data['id']} :[{data['nazev']}] {data['topic']}")
                            except Exception:
                                if config.DEBUG:
                                    tb.print_exc()
                    else:
                        # Just viewing topic
                        try:
                            response = self.get_url(f"{self.system.url}/get-room?id={int(room_id)}")
                            data = json.loads(response)
                            self.send(self.rfc.RPL_TOPIC, f"#{data['id']} :[{data['nazev']}] {data['topic']}")
                        except Exception:
                            if config.DEBUG:
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
                self.send(self.rfc.RPL_VERSION, f"ChatujmeGW-{config.VERSION}.{config.DEBUG} {self.user.me} :Python {sys.version.split()[0]} on {sys.platform}")

            elif command == "MOTD":
                # Send MOTD on request
                self.send(self.rfc.RPL_MOTDSTART, f":- {self.user.me} Message of the Day -")
                for line in MOTD_LINES:
                    formatted = line.format(user=self.user.username, sex=self.user.sex, host=self.user.me, version=config.VERSION)
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

        if config.DEBUG:
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
                        # Old cleanup must not part rooms the new session is (re)joining
                        old_handler.instance.rooms = []
                    try:
                        # Close the socket so the old thread's blocking recv exits now,
                        # not after the 300s timeout
                        old_handler.socket.close()
                    except Exception:
                        pass
                state.active_connections[nick_lower] = self.parent

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
        if len(msg) > config.MAX_LINE_LENGTH + 2:  # +2 for \r\n
            msg = msg[:config.MAX_LINE_LENGTH] + "\r\n"
        if config.DEBUG:
            log(f">> {msg.strip()}")
        try:
            # Lock + sendall: two threads (parse + GetMessages) share this socket;
            # a bare send() may write partially and interleave lines
            with self.send_lock:
                self.socket.sendall(msg.encode('utf-8'))
        except Exception as e:
            if config.DEBUG:
                log(f"Send error: {e}")
