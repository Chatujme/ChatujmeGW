"""Background threads: per-connection message poller and thread janitor."""

import json
import random
import re
import threading
import time
import traceback as tb

from . import config, state
from . import textfilters
from .models import UserInRoom
from .util import log


class Collector(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.running = True
        self.daemon = True
        if config.DEBUG and config.VERBOSE_THREADS:
            log("Collector initialized")

    def run(self):
        if config.DEBUG and config.VERBOSE_THREADS:
            log("Collector started")
        while self.running:
            with state.thread_lock:
                thread_count = len(state.World.vlakna)
                for thread in state.World.vlakna[:]:
                    if not thread.is_alive() and thread._started.is_set():
                        state.World.vlakna.remove(thread)
                        if config.DEBUG and config.VERBOSE_THREADS:
                            log(f"Collector purging {thread}")
                        thread_count -= 1
            if config.DEBUG and config.VERBOSE_THREADS:
                log(f"Collector: all clear ({thread_count} threads)")
            time.sleep(5)

        # shutdown
        with state.thread_lock:
            for thread in state.World.vlakna:
                thread.running = False
        if config.DEBUG:
            log("Collector shutdown")

    def start_threads(self):
        with state.thread_lock:
            try:
                for thread in state.World.vlakna:
                    if not thread._started.is_set():
                        thread.start()
            except Exception as e:
                log(f"Thread failed to start: {e}")


class GetMessages(threading.Thread):
    def __init__(self, inst):
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
                    response = self.inst.api.get_messages(room.id, room.last_id)
                    data = json.loads(response)

                    # Debug: log every API response structure
                    if config.DEBUG >= 2:
                        log(f"[GET-MSGS] #{room.id}: code={data.get('code', 'N/A')} mess_count={len(data.get('mess', []))}")

                    # Check if API returned an error code (kicked from room)
                    if 'code' in data:
                        error_code = int(data.get('code', 0))
                        # 403 = kicked/banned from room, 404 = room doesn't exist
                        if error_code in (403, 404):
                            error_msg = data.get('message', 'Unknown error')
                            if config.DEBUG:
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
                    if config.DEBUG:
                        tb.print_exc()
                    data = {'mess': []}

                try:
                    for mess in data.get('mess', []):
                        if config.DEBUG >= 2:
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

                        msg = textfilters.clean_message(mess['zprava'], self.inst.user.show_smiles)

                        if mess["typ"] == 0:  # Public
                            self.inst.send_raw(
                                f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG #{room.id} :{msg}\r\n"
                            )
                        elif mess["typ"] == 1:  # PM
                            self.inst.send_raw(
                                f":{self.inst.make_hostmask(mess['nick'], room.id)} PRIVMSG {mess['komu']} :{msg}\r\n"
                            )
                        elif mess["typ"] == 2:  # System
                            if config.DEBUG:
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
                    if config.DEBUG:
                        tb.print_exc()

                # Idler - use sayAgo from API (server-side idle time)
                say_ago = data.get('sayAgo', {})
                try:
                    say_ago_seconds = int(say_ago.get('min', 0)) * 60 + int(say_ago.get('sec', 0))
                except (ValueError, TypeError):
                    say_ago_seconds = 0
                room.say_ago_seconds = say_ago_seconds  # Store for STATUS command

                if self.inst.user.idler_timer != 0 and self.inst.user.idler_enable:
                    if config.DEBUG >= 2:
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
        if len(msg) > config.MAX_MESSAGE_LENGTH:
            msg = msg[:config.MAX_MESSAGE_LENGTH]
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
                if config.DEBUG:
                    tb.print_exc()

        elif "odešel" in t or "odešla" in t:
            try:
                nick = re.findall(r'.+\s(.+)\s(odešel|odešla)', msg)[0]
                partmess = "Left" if nick[1] == "odešel" else "Left"
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick[0], room.id)} PART #{room.id} :{partmess}\r\n"
                )
            except Exception:
                if config.DEBUG:
                    tb.print_exc()

        elif "odstraněn" in t:
            try:
                nick = re.findall(r'.+e(lka|l)\s(.+)\sby(la|l)\s', msg)[0]
                nick = nick[1]
                self.inst.send_raw(
                    f":{self.inst.make_hostmask(nick, room.id)} PART #{room.id} :Inactive\r\n"
                )
            except Exception:
                if config.DEBUG:
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
                if config.DEBUG:
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
                if config.DEBUG:
                    tb.print_exc()

        else:
            # Generic system message as NOTICE
            # Remove timestamp prefix like "19:10:13: " from the beginning
            clean_msg = re.sub(r'^\d{1,2}:\d{2}:\d{2}:\s*', '', msg)
            self.inst.send_raw(
                f":{self.inst.user.me} NOTICE #{room.id} :{clean_msg}\r\n"
            )
