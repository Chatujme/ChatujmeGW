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

import copy
import io
import os
import re
import socket
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
BIND = "127.0.0.1"  # Security: localhost only by default, use --listen 0.0.0.0 for external access
VERSION = "3.0.0"
UA = f'ChatujmeGW/v{VERSION} ({sys.platform} {os.name}) Python {sys.version.split(" ")[0]}'

# Security: Max retry attempts for API calls
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Validation limits (from Chatujme.cz registration)
MIN_NICK_LENGTH = 4
MAX_NICK_LENGTH = 23
MAX_ROOM_ID = 999999

# Thread synchronization
thread_lock = threading.Lock()


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
args = parser.parse_args()

PORT = args.port
BIND = args.listen
DEBUG = args.debug
VERBOSE_THREADS = DEBUG >= 2

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
    RPL_NOTICE = "NOTICE"
    RPL_JOIN = "JOIN"
    RPL_PART = "PART"
    RPL_MODE = "MODE"
    RPL_KICK = "KICK"
    RPL_PRIVMSG = "PRIVMSG"


# MOTD lines (without empty lines for RFC compliance)
MOTD_LINES = [
    "  .g8\"\"\"bgd` MM             Welcome to Chatujme.cz",
    ".dP'     `M  MM             Logged in as {user}@{host}",
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
        self.cookie_jar = http.cookiejar.LWPCookieJar(os.path.join(PATH, "cookies.txt"))
        self.url_fetcher = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.settings_show_pm_from = True
        self.timer = 5
        self.idler_enable = False
        self.idler_timer = 2400  # 40min
        self.idler_text = [".", "..", "AFK"]
        self.show_smiles = 1  # 0 - Hide, 1 - Text, 2 - URL


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


def log(text):
    text = str(text).replace("\r", "").replace("\n", " | ")
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
                        # Skip messages from ourselves, but NOT system messages (typ 2)
                        # System messages should always be processed (kicks, joins, etc.)
                        if mess["typ"] != 2:
                            if mess['nick'].lower() == self.inst.user.username.lower():
                                continue
                            if mess['nick'].lower() == self.inst.user.nick.lower():
                                continue

                        room.last_id = mess['id']
                        room.last_mess = mess['zprava']

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

                except Exception as e:
                    if DEBUG:
                        tb.print_exc()
                    self.handle_error(data, room)

                # Idler
                my_time = time.time()
                if (my_time - room.idler_lastsend) >= self.inst.user.idler_timer and \
                   self.inst.user.idler_timer != 0 and self.inst.user.idler_enable:
                    self.inst.send_raw(
                        f":{self.inst.user.me} NOTICE #{room.id} :Idler message sent\r\n"
                    )
                    room.idler_lastsend = time.time()
                    self.inst.send_text(random.choice(self.inst.user.idler_text), room.id, room.id)

            # Load users on first load
            for room in self.inst.rooms:
                if room.first_load:
                    self.inst.reload_users(room.id)
                    room.first_load = False

            time.sleep(self.inst.user.timer)

    def handle_system_message(self, mess, msg, room):
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
            clean_msg = re.sub(r'(.*?):\s*', '', msg)
            self.inst.send_raw(
                f":{self.inst.user.me} NOTICE #{room.id} :{clean_msg}\r\n"
            )

    def handle_error(self, data, room):
        try:
            code = data.get('code')
            if code == "404" or code == "403":
                self.inst.send_raw(f":{self.inst.user.me} PART #{room.id}\r\n")
                log(f"User {self.inst.user.username} left room")
                self.inst.part(room.id)
            elif code == "401":
                self.inst.user.login = False
                self.inst.send_raw(
                    f":{self.inst.user.me} NOTICE #{room.id} :Attempting re-login...\r\n"
                )
                self.inst.user.login = self.inst.check_login()
                if self.inst.user.login:
                    self.inst.send_raw(
                        f":{self.inst.user.me} NOTICE #{room.id} :Re-login successful\r\n"
                    )
                else:
                    self.inst.send_raw(
                        f":{self.inst.user.me} NOTICE #{room.id} :Re-login failed\r\n"
                    )
                    time.sleep(10)
        except Exception:
            if DEBUG:
                tb.print_exc()
            time.sleep(1)


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

    def clean_highlight(self, msg):
        return re.sub(r"<span style='background:#eded1a'>([^<]+)</span>", r"\1", msg)

    def clean_urls(self, msg):
        return re.sub(r'<a href="([^"]+)" target="_blank">([^<]+)</a>', r"\1", msg)

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

    def get_url(self, url, retry_count=0):
        """Fetch URL with retry limit to prevent infinite loops"""
        self.user.url_fetcher.addheaders = [('User-agent', UA)]
        try:
            response = self.user.url_fetcher.open(url, timeout=30)
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
        self.user.url_fetcher.addheaders = [('User-agent', UA)]
        try:
            response = self.user.url_fetcher.open(url, data=postdata.encode('utf-8'), timeout=30)
            return response.read().decode('utf-8')
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

    def check_login(self):
        if not self.user.username or not self.user.nick or not self.user.password:
            if DEBUG:
                log(f"[LOGIN] Missing credentials: user={self.user.username}, nick={self.user.nick}, pass={'*' * len(self.user.password) if self.user.password else 'None'}")
            return False

        if DEBUG:
            log(f"[LOGIN] Attempting login for {self.user.username}")

        # Create user-specific cookie file
        cookie_path = os.path.join(PATH, f"cookies_{self.user.username}.txt")
        self.user.cookie_jar = http.cookiejar.LWPCookieJar(cookie_path)
        self.user.url_fetcher = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.user.cookie_jar)
        )

        try:
            postdata = f"username={self.user.username}&password={self.user.password}"
            response = self.post_url(f"{self.system.url}/check-login", postdata)
            if DEBUG:
                log(f"[LOGIN] API response: {response[:200]}")
            data = json.loads(response)

            if data['code'] == 401:
                self.send(self.rfc.ERR_NOLOGIN, f"{self.user.username} :{data['message']}")
                return False
            elif data['code'] in (200, 201):
                self.send_welcome()
                log(f"User logged in: {self.user.username}")
                return True
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
            formatted = line.format(user=self.user.username, host=self.user.me, version=VERSION)
            self.send(self.rfc.RPL_MOTD, f":- {formatted}")
        self.send(self.rfc.RPL_ENDOFMOTD, ":End of /MOTD command")

    def is_in_room(self, room, rtn=False):
        for croom in self.rooms:
            if int(room) == int(croom.id):
                return croom if rtn else True
        return False

    def join_to_room(self, room_id, key=None):
        response = self.get_url(f"{self.system.url}/join?id={room_id}")
        return json.loads(response)

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
        self.user.url_fetcher.addheaders = [('User-agent', UA)]
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
        postdata = f"roomId={room_id}&text={urllib.parse.quote_plus(text)}&target={target}"
        response = self.post_url(f"{self.system.url}/post-text", postdata)
        try:
            return json.loads(response)
        except Exception:
            return []

    def parse(self, data, timestamp):
        if not data:
            self.connection = False
            return 2

        lines = data.replace("\r\n", "\n").split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = line.split(" ")
            command = parts[0].upper()

            if DEBUG:
                log(f"<< {line}")

            # CAP handling for modern IRC clients (like girc)
            if command == "CAP":
                self.handle_cap(parts)

            elif command == "NICK":
                if len(parts) < 2:
                    continue
                if self.user.login:
                    self.send_raw(f":{self.user.me} NOTICE {self.user.username} :Already logged in\r\n")
                    continue
                nick = parts[1]
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
                self.user.password = parts[1]
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
                    self.part(room_id)

            elif command == "PING":
                token = parts[1] if len(parts) >= 2 else self.user.me
                self.send_raw(f":{self.user.me} PONG {self.user.me} :{token}\r\n")
                try:
                    self.get_url(f"{self.system.url}/ping")
                except Exception:
                    pass

            elif command == "PONG":
                pass  # Ignore PONG responses

            elif command == "LIST":
                rooms = self.system.get_rooms()
                self.send(self.rfc.RPL_LISTSTART, "Channel :Users Name")
                for room in rooms:
                    self.send(self.rfc.RPL_LIST, f"#{room['id']} {room['online']} :{room['nazev']}")
                self.send(self.rfc.RPL_LISTEND, ":End of /LIST")

            elif command == "MODE":
                if len(parts) >= 2:
                    self.send(self.rfc.RPL_CHANNELMODEIS, f"{parts[1]} +tn")

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
                target = parts[1]
                # Get message after the colon
                msg_start = line.find(':', 1)
                if msg_start == -1:
                    text = ' '.join(parts[2:])
                else:
                    text = line[msg_start + 1:]

                is_pm = not target.startswith('#')

                # Handle NickServ REGISTER command
                if target.lower() == "nickserv" and text.lower().startswith("register"):
                    self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Registration is only available via web.\r\n")
                    self.send_raw(f":NickServ!services@{self.user.me} NOTICE {self.user.nick} :Please visit: https://chatujme.cz/registrace\r\n")
                    continue

                if is_pm:
                    text = f"/m {target} {text}"
                    if self.rooms:
                        room_id = self.rooms[0].id
                        self.rooms[0].idler_lastsend = time.time()
                    else:
                        continue
                else:
                    room_id = target.lstrip('#')
                    room = self.is_in_room(room_id, True)
                    if room:
                        room.idler_lastsend = time.time()

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
                    try:
                        response = self.get_url(f"{self.system.url}/get-room?id={int(room_id)}")
                        data = json.loads(response)
                        self.send(self.rfc.RPL_TOPIC, f"#{data['id']} :[{data['nazev']}] {data['topic']}")
                    except Exception:
                        if DEBUG:
                            tb.print_exc()

            elif command == "WHOIS":
                if len(parts) >= 2:
                    nick = parts[1]
                    self.send_raw(
                        f":{self.user.me} {self.rfc.RPL_WHOISUSER} {self.user.username} {nick} ~{nick} {self.user.me} * :{nick}\r\n"
                    )
                    self.send(self.rfc.RPL_ENDOFWHOIS, f"{nick} :End of /WHOIS list")

            elif command == "USERHOST":
                if len(parts) >= 2:
                    nick = parts[1]
                    self.send(self.rfc.RPL_USERHOST, f"{nick}=+~{nick}@{self.user.me}")

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

            elif command not in ("", "CAP"):
                self.send(self.rfc.ERR_UNKNOWNCOMMAND, f"{command} :Unknown command")

    def handle_cap(self, parts):
        """Handle CAP negotiation for modern IRC clients"""
        if len(parts) < 2:
            return

        subcmd = parts[1].upper()
        if subcmd == "LS":
            # Send empty capability list
            self.send_raw(f":{self.user.me} CAP * LS :\r\n")
            self.cap_negotiating = True
        elif subcmd == "REQ":
            # Reject all capability requests (we don't support any)
            caps = ' '.join(parts[2:]).lstrip(':') if len(parts) > 2 else ""
            self.send_raw(f":{self.user.me} CAP * NAK :{caps}\r\n")
        elif subcmd == "END":
            self.cap_negotiating = False
            # If we have credentials and not already logged in, try to login
            if not self.user.login and self.user.password and self.user.nick and self.user.username:
                self.user.login = self.check_login()

    def handle_join(self, room):
        in_room = self.is_in_room(room)
        data = self.join_to_room(room)

        if DEBUG:
            log(f"JOIN to {room}: {data}")

        if data['code'] == 403:
            # Banned from channel - send both RFC error and NOTICE with detailed message
            ban_msg = data.get('message', 'Cannot join channel')
            self.send(self.rfc.ERR_BANNEDFROMCHAN, f"#{data.get('id', room)} :Cannot join channel (+b)")
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :{ban_msg}\r\n")
        elif data['code'] == 404:
            self.send(self.rfc.ERR_NOSUCHCHANNEL, f"#{room} :No such channel")
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :{data.get('message', 'Room does not exist')}\r\n")
        elif data['code'] != 200:
            # Unknown error code - show as notice
            err_msg = data.get('message', f'Unknown error (code {data["code"]})')
            self.send_raw(f":{self.user.me} NOTICE {self.user.nick} :Error joining #{room}: {err_msg}\r\n")
        else:
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
        """Send raw IRC message"""
        if DEBUG:
            log(f">> {msg.strip()}")
        try:
            self.socket.send(msg.encode('utf-8'))
        except Exception as e:
            if DEBUG:
                log(f"Send error: {e}")


class SocketHandler(threading.Thread):
    def __init__(self, sock, address):
        threading.Thread.__init__(self)
        self.socket = sock
        self.address = address
        self.running = True
        self.daemon = True

    def run(self):
        log(f"Connection accepted from {self.address[0]}")
        instance = Chatujme(self.socket, self.address[0], self)
        instance.send_raw(f":{instance.user.me} NOTICE * :Connected from {self.address[0]}, waiting for login.\r\n")

        while self.running:
            timestamp = int(time.time())
            try:
                ircdata = instance.socket.recv(2 ** 13).decode('utf-8', errors='replace')
                if DEBUG:
                    log(f"[RECV] {repr(ircdata)}")
                result = instance.parse(ircdata, timestamp)
                if DEBUG:
                    log(f"[PARSE] result={result}, login={instance.user.login}, nick={instance.user.nick}")
                if result == 2:
                    if DEBUG:
                        log("[PARSE] Breaking due to result=2")
                    break
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

        log(f"Connection from {self.address[0]} closed.")
        try:
            self.socket.close()
        except Exception:
            pass


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Platform-specific socket options
    if sys.platform == 'win32':
        # Windows: SO_EXCLUSIVEADDRUSE prevents port hijacking
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # Linux/Mac: SO_REUSEADDR allows quick restart after crash
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    s.settimeout(1.0)  # Allow periodic check for shutdown

    try:
        s.bind((BIND, PORT))
    except OSError as e:
        if e.errno == 10048 or e.errno == 98:  # Windows WSAEADDRINUSE / Linux EADDRINUSE
            log(f"ERROR: Port {PORT} is already in use. Another instance running?")
        else:
            log(f"ERROR: Cannot bind to {BIND}:{PORT} - {e}")
        fatal_error_pause()
        sys.exit(1)

    s.listen(50)

    World.collector = Collector()
    World.collector.start()

    log(f"ChatujmeGW {VERSION} (Python 3), listening on {BIND}:{PORT}")

    try:
        while World.collector.running:
            try:
                connection, address = s.accept()
                connection.settimeout(300)  # 5 min timeout for client connections
                with thread_lock:
                    if len(World.vlakna) <= 378:
                        handler = SocketHandler(connection, address)
                        World.vlakna.append(handler)
                    else:
                        connection.close()
                        continue
                World.collector.start_threads()
            except socket.timeout:
                continue  # Normal timeout, check if still running
            except Exception as e:
                if DEBUG:
                    log(f"Accept error: {e}")
    except KeyboardInterrupt:
        log("Received shutdown signal...")
    finally:
        World.collector.running = False
        s.close()
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
