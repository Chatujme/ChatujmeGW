"""Background threads: per-connection message poller and thread janitor."""

import json
import random
import re
import threading
import time
import traceback as tb

from . import config, state
from . import textfilters
from .models import ChannelMember
from .util import log, sanitize_irc


class ThreadJanitor(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.running = True
        self.daemon = True
        if config.DEBUG and config.VERBOSE_THREADS:
            log("ThreadJanitor initialized")

    def run(self):
        if config.DEBUG and config.VERBOSE_THREADS:
            log("ThreadJanitor started")
        while self.running:
            with state.threads_lock:
                thread_count = len(state.threads)
                for thread in state.threads[:]:
                    if not thread.is_alive() and thread._started.is_set():
                        state.threads.remove(thread)
                        if config.DEBUG and config.VERBOSE_THREADS:
                            log(f"ThreadJanitor purging {thread}")
                        thread_count -= 1
            if config.DEBUG and config.VERBOSE_THREADS:
                log(f"ThreadJanitor: all clear ({thread_count} threads)")
            time.sleep(5)

        # shutdown
        with state.threads_lock:
            for thread in state.threads:
                thread.running = False
        if config.DEBUG:
            log("ThreadJanitor shutdown")

    def start_threads(self):
        with state.threads_lock:
            try:
                for thread in state.threads:
                    if not thread._started.is_set():
                        thread.start()
            except Exception as e:
                log(f"Thread failed to start: {e}")


class MessagePoller(threading.Thread):
    def __init__(self, session):
        threading.Thread.__init__(self)
        self.session = session
        self.running = True
        self.daemon = True

    def run(self):
        while self.running and self.session.connection:
            if len(self.session.channels) == 0:
                time.sleep(5)
                continue
            if not self.session.connection:
                return

            for channel in self.session.channels[:]:  # Copy list to allow modification during iteration
                try:
                    response = self.session.api.get_messages(channel.id, channel.last_id)
                    data = json.loads(response)

                    # Debug: log every API response structure
                    if config.DEBUG >= 2:
                        log(f"[GET-MSGS] #{channel.id}: code={data.get('code', 'N/A')} mess_count={len(data.get('mess', []))}")

                    # Check if API returned an error code (kicked from room)
                    if 'code' in data:
                        error_code = int(data.get('code', 0))
                        # 403 = kicked/banned from room, 404 = room doesn't exist
                        if error_code in (403, 404):
                            error_msg = data.get('message', 'Unknown error')
                            if config.DEBUG:
                                log(f"[KICKED] Removed from #{channel.id}: {error_msg}")
                            self.session.send_raw(
                                f":{self.session.server_name} KICK #{channel.id} {self.session.user.nick} :{error_msg}\r\n"
                            )
                            # Remove from rooms list directly (part will fail since we're banned)
                            known = self.session.find_channel(channel.id)
                            if known:
                                self.session.channels.remove(known)
                                self.session.send_raw(f":{self.session.user.nick} PART #{channel.id}\r\n")
                        elif error_code == 401:
                            # Session expired - re-login and retry next poll
                            self.session.reauthenticate()
                        continue
                except Exception:
                    if config.DEBUG:
                        tb.print_exc()
                    data = {'mess': []}

                try:
                    for message in data.get('mess', []):
                        if config.DEBUG >= 2:
                            log(f"[API MSG] id={message['id']} typ={message['typ']} nick={message['nick']} msg={message['zprava'][:50]}...")
                        if int(channel.last_id) >= int(message['id']):
                            continue
                        channel.last_id = message['id']

                        # Skip messages from ourselves, but NOT system messages (typ 2)
                        # System messages should always be processed (kicks, joins, etc.)
                        if message["typ"] != 2:
                            if message['nick'].lower() == self.session.user.username.lower():
                                continue
                            if message['nick'].lower() == self.session.user.nick.lower():
                                continue

                        if channel.first_load:
                            continue

                        # Security: strip CR/LF from API-sourced fields before framing
                        # them into IRC lines, otherwise a newline in a message could
                        # inject a forged PRIVMSG/KICK/etc. into the client's stream
                        msg = textfilters.clean_message(message['zprava'], self.session.user.show_smiles)
                        msg = sanitize_irc(msg)
                        nick = sanitize_irc(message['nick'])
                        komu = sanitize_irc(str(message.get('komu', '')))

                        if message["typ"] == 0:  # Public
                            self.session.send_raw(
                                f":{self.session.make_hostmask(nick, channel.id)} PRIVMSG #{channel.id} :{msg}\r\n"
                            )
                        elif message["typ"] == 1:  # PM
                            self.session.send_raw(
                                f":{self.session.make_hostmask(nick, channel.id)} PRIVMSG {komu} :{msg}\r\n"
                            )
                        elif message["typ"] == 2:  # System
                            if config.DEBUG:
                                log(f"[SYSTEM MSG] nick={nick} msg={msg}")
                            self.handle_system_message(msg, channel)
                        elif message["typ"] == 3:  # WALL
                            if self.session.user.settings_show_pm_from:
                                rname = sanitize_irc(str(message.get('rname', '')))
                                rid = sanitize_irc(str(message.get('rid', '')))
                                self.session.send_raw(
                                    f":{self.session.make_hostmask(nick, channel.id)} PRIVMSG {komu} :[From room {rname} #{rid}] {msg}\r\n"
                                )
                            else:
                                self.session.send_raw(
                                    f":{self.session.make_hostmask(nick, channel.id)} PRIVMSG {komu} :{msg}\r\n"
                                )
                        elif message["typ"] == 10:  # ACTION (/me command)
                            self.session.send_raw(
                                f":{self.session.make_hostmask(nick, channel.id)} PRIVMSG #{channel.id} :\x01ACTION {msg}\x01\r\n"
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
                channel.say_ago_seconds = say_ago_seconds  # Store for STATUS command

                if self.session.user.idler_timer != 0 and self.session.user.idler_enable:
                    if config.DEBUG >= 2:
                        log(f"[IDLER] #{channel.id}: sayAgo={say_ago_seconds}s, timer={self.session.user.idler_timer}s")
                    # Send idler message when idle time reached
                    if say_ago_seconds >= self.session.user.idler_timer:
                        self.session.send_raw(
                            f":{self.session.server_name} NOTICE #{channel.id} :Idler message sent (idle {say_ago_seconds}s)\r\n"
                        )
                        channel.idler_last_sent = time.time()
                        self.session.send_text(random.choice(self.session.user.idler_text), channel.id, channel.id)

            # Away message repeater (every 30 min)
            if self.session.user.away_message:
                my_time = time.time()
                if (my_time - self.session.user.away_last_sent) >= self.session.user.away_interval:
                    for channel in self.session.channels:
                        self.session.send_text(self.session.user.away_message, channel.id, channel.id)
                    self.session.user.away_last_sent = my_time
                    self.session.send_raw(
                        f":{self.session.server_name} NOTICE {self.session.user.nick} :Away message repeated to all rooms\r\n"
                    )

            # Load users on first load
            for channel in self.session.channels:
                if channel.first_load:
                    self.session.send_names(channel.id)
                    channel.first_load = False

            # Server-side PING to keep connection alive + PONG timeout check
            if not self.session.ping_keepalive(time.time()):
                log(f"[TIMEOUT] {self.session.user.nick}: no PONG received within {self.session.user.pong_timeout}s, disconnecting")
                self.session.send_raw(f"ERROR :Closing link: PONG timeout ({self.session.user.pong_timeout}s)\r\n")
                self.session.parent.running = False
                self.session.connection = False
                return

            time.sleep(self.session.user.poll_interval)

    @staticmethod
    def _remove_member(channel, nick):
        """Drop a member from the cached roster (keeps NAMES/WHOIS/hostmask fresh)."""
        low = nick.lower()
        channel.members = [m for m in channel.members if m.nick.lower() != low]

    def handle_system_message(self, msg, channel):
        # Security: Limit message length to prevent ReDoS attacks
        if len(msg) > config.MAX_MESSAGE_LENGTH:
            msg = msg[:config.MAX_MESSAGE_LENGTH]
        t = msg.replace("'", "")
        member = ChannelMember()

        if "vstoupil" in t or "vstoupila" in t:
            try:
                ret = re.findall(r'.+\s(.+)\svstoupi(la|l)', msg)[0]
                nick = ret[0]
                member.nick = nick
                member.sex = "girls" if ret[1] == "la" else "boys"
                channel.members.append(member)
                self.session.send_raw(
                    f":{self.session.make_hostmask(nick, channel.id)} JOIN #{channel.id}\r\n"
                )
            except Exception:
                if config.DEBUG:
                    tb.print_exc()

        elif "odešel" in t or "odešla" in t:
            try:
                nick = re.findall(r'.+\s(.+)\s(odešel|odešla)', msg)[0]
                self._remove_member(channel, nick[0])
                self.session.send_raw(
                    f":{self.session.make_hostmask(nick[0], channel.id)} PART #{channel.id} :Left\r\n"
                )
            except Exception:
                if config.DEBUG:
                    tb.print_exc()

        elif "odstraněn" in t:
            try:
                nick = re.findall(r'.+e(lka|l)\s(.+)\sby(la|l)\s', msg)[0]
                nick = nick[1]
                self._remove_member(channel, nick)
                self.session.send_raw(
                    f":{self.session.make_hostmask(nick, channel.id)} PART #{channel.id} :Inactive\r\n"
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
                self._remove_member(channel, target)
                self.session.send_raw(
                    f":{self.session.make_hostmask(kicker, channel.id)} KICK #{channel.id} {target} :{duvod}\r\n"
                )
                # If the kicked user is us, leave the room
                if target.lower() == self.session.user.username.lower() or target.lower() == self.session.user.nick.lower():
                    log(f"Kicked from #{channel.id} by {kicker}: {duvod}")
                    self.session.part_channel(channel.id)
            except Exception:
                if config.DEBUG:
                    tb.print_exc()

        elif "předal" in t or "předala" in t:
            try:
                nick = re.findall(r'.+e(lka|l)\s(.+)\spředa(l|la)\ssprávce\s(.+)', msg)[0]
                target = nick[3]
                nick = nick[1]
                self.session.send_raw(
                    f":{self.session.make_hostmask(nick, channel.id)} MODE #{channel.id} -h {nick}\r\n"
                )
                self.session.send_raw(
                    f":{self.session.make_hostmask(nick, channel.id)} MODE #{channel.id} +h {target}\r\n"
                )
                self.session.send_names(channel.id)
            except Exception:
                if config.DEBUG:
                    tb.print_exc()

        else:
            # Generic system message as NOTICE
            # Remove timestamp prefix like "19:10:13: " from the beginning
            clean_msg = re.sub(r'^\d{1,2}:\d{2}:\d{2}:\s*', '', msg)
            self.session.send_raw(
                f":{self.session.server_name} NOTICE #{channel.id} :{clean_msg}\r\n"
            )
