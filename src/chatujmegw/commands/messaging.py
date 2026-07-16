"""PRIVMSG/NOTICE handling including CTCP requests and replies."""

import sys
import time

from .. import config, numerics
from ..util import log, sanitize_irc


def _handle_ctcp(sess, command, target, text):
    """Returns True when the message was a CTCP payload and has been handled."""
    if not (text.startswith('\x01') and text.endswith('\x01')):
        return False

    ctcp_content = text.strip('\x01')
    ctcp_parts = ctcp_content.split(' ', 1)
    ctcp_cmd = ctcp_parts[0].upper()

    # NOTICE with CTCP = reply from client
    if command == "NOTICE":
        if ctcp_cmd == "VERSION" and len(ctcp_parts) > 1:
            # Client sent VERSION reply - store it
            sess.user.client_version = ctcp_parts[1]
            log(f"Client version for {sess.user.nick}: {sess.user.client_version}")
        return True

    # PRIVMSG with CTCP = request to server
    if ctcp_cmd == "VERSION":
        # Reply with CTCP VERSION response
        version_reply = f"ChatujmeGW {config.VERSION} - Python {sys.version.split()[0]} on {sys.platform}"
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :\x01VERSION {version_reply}\x01\r\n")
    elif ctcp_cmd == "PING":
        # Echo back PING for latency measurement
        ping_data = text.strip('\x01')[5:].strip()  # Get data after "PING "
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :\x01PING {ping_data}\x01\r\n")
    elif ctcp_cmd == "TIME":
        # Reply with current time
        time_str = time.strftime("%a %b %d %H:%M:%S %Y")
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :\x01TIME {time_str}\x01\r\n")
    elif ctcp_cmd == "ACTION":
        # Handle /me command - convert to Chatujme.cz /me format
        action_text = text.strip('\x01')[7:]  # Get text after "ACTION "
        if action_text and target.startswith('#'):
            room_id = target.lstrip('#')
            channel = sess.find_channel(room_id)
            if channel:
                channel.idler_last_sent = time.time()
                sess.send_text(f"/me {action_text}", room_id, target)
                # Auto-disable away
                if sess.user.away_message:
                    sess.user.away_message = None
                    sess.user.away_last_sent = 0
                    sess.send_numeric(numerics.RPL_UNAWAY, ":You are no longer marked as being away")
    # Other CTCP commands are ignored
    return True


def privmsg(sess, parts, line):
    if len(parts) < 3:
        return
    if not sess.user.login:
        sess.send_numeric(numerics.ERR_NOLOGIN, ":You have not registered")
        return

    command = parts[0].upper()
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
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Message truncated to {config.MAX_MESSAGE_LENGTH} characters\r\n")

    if _handle_ctcp(sess, command, target, text):
        return

    is_pm = not target.startswith('#')

    # Handle NickServ REGISTER command
    if target.lower() == "nickserv" and text.lower().startswith("register"):
        sess.send_raw(f":NickServ!services@{sess.server_name} NOTICE {sess.user.nick} :Registration is only available via web.\r\n")
        sess.send_raw(f":NickServ!services@{sess.server_name} NOTICE {sess.user.nick} :Please visit: https://chatujme.cz/registrace\r\n")
        return

    if is_pm:
        if not sess.channels:
            sess.send_numeric(numerics.ERR_NOSUCHNICK, f"{target} :Cannot send PM - join a room first")
            return
        text = f"/m {target} {text}"
        room_id = sess.channels[0].id
        sess.channels[0].idler_last_sent = time.time()
    else:
        room_id = target.lstrip('#')
        channel = sess.find_channel(room_id)
        if channel:
            channel.idler_last_sent = time.time()

    # Auto-disable away when user sends a message
    if sess.user.away_message:
        sess.user.away_message = None
        sess.user.away_last_sent = 0
        sess.send_numeric(numerics.RPL_UNAWAY, ":You are no longer marked as being away")
        for _channel in sess.channels:
            sess.send_raw(f":{sess.user.nick}!{sess.user.nick}@{sess.server_name} AWAY\r\n")

    sess.send_text(text, room_id, target)
