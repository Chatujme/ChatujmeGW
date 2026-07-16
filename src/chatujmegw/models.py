"""Data holders: the connected user, joined channels and their members.

Naming follows RFC 2812 / modern IRC spec terminology: a connected entity
is a client/user, Chatujme rooms map to IRC channels, people in them are
channel members ("boys"/"girls" values come from the Chatujme API contract).
"""

import http.cookiejar
import urllib.request


class User:
    def __init__(self):
        self.username = ""
        self.nick = ""
        self.password = ""
        self.login = False
        self.sex = "boys"
        self.polling = False  # message poller thread started
        # Security: Use in-memory cookies only - no file storage
        self.cookie_jar = http.cookiejar.CookieJar()
        self.url_fetcher = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        # Security: Command rate limiting per connection
        self.command_timestamps = []
        self.last_command_check = 0
        self.settings_show_pm_from = True
        self.poll_interval = 5  # seconds between get-messages polls
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


class ChannelMember:
    def __init__(self):
        self.nick = ""
        self.sex = ""


class Channel:
    def __init__(self):
        self.id = None
        self.members = []
        self.last_id = 0  # poll cursor: id of the last message seen
        self.first_load = True
        self.idler_last_sent = 0
        self.say_ago_seconds = 0  # Server-side idle time from API
