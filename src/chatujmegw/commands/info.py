"""Informational commands: PING/PONG, VERSION, MOTD, USERHOST, WHOIS."""

import sys
import time

from .. import config
from ..numerics import MOTD_LINES


def ping(sess, parts, line):
    token = parts[1] if len(parts) >= 2 else sess.user.me
    sess.send_raw(f":{sess.user.me} PONG {sess.user.me} :{token}\r\n")
    try:
        sess.api.ping()
    except Exception:
        pass


def pong(sess, parts, line):
    # Track PONG for timeout detection
    sess.user.last_pong_received = time.time()
    sess.user.pending_ping_token = None


def version(sess, parts, line):
    # RPL_VERSION (351): <version>.<debuglevel> <server> :<comments>
    sess.send(sess.rfc.RPL_VERSION, f"ChatujmeGW-{config.VERSION}.{config.DEBUG} {sess.user.me} :Python {sys.version.split()[0]} on {sys.platform}")


def motd(sess, parts, line):
    # Send MOTD on request
    sess.send(sess.rfc.RPL_MOTDSTART, f":- {sess.user.me} Message of the Day -")
    for banner_line in MOTD_LINES:
        formatted = banner_line.format(user=sess.user.username, sex=sess.user.sex, host=sess.user.me, version=config.VERSION)
        sess.send(sess.rfc.RPL_MOTD, f":- {formatted}")
    sess.send(sess.rfc.RPL_ENDOFMOTD, ":End of /MOTD command")


def userhost(sess, parts, line):
    if len(parts) >= 2:
        nick = parts[1]
        sess.send(sess.rfc.RPL_USERHOST, f"{nick}=+~{nick}@{sess.user.me}")


def whois(sess, parts, line):
    """Gather info about a user from all joined rooms"""
    if len(parts) < 2:
        return
    nick = parts[1].lstrip(':')

    found = False
    user_sex = "users"
    user_rooms = []
    user_status = {}  # room_id -> status (op/halfop/owner)

    # Search in all joined rooms
    for room in sess.rooms:
        for u in room.users:
            if u.nick.lower() == nick.lower():
                found = True
                user_sex = u.sex
                # Get status from API
                try:
                    users_data = sess.get_room_users(room.id)
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
        sess.send(sess.rfc.ERR_NOSUCHNICK, f"{nick} :No such nick/channel")
        return

    # Build realname based on sex
    realname = "Male user" if user_sex == "boys" else "Female user" if user_sex == "girls" else "User"

    # 311 RPL_WHOISUSER: <nick> <user> <host> * :<realname>
    sess.send(sess.rfc.RPL_WHOISUSER, f"{nick} ~{nick} {user_sex}.chatujme.cz * :{realname}")

    # 312 RPL_WHOISSERVER: <nick> <server> :<server info>
    sess.send(sess.rfc.RPL_WHOISSERVER, f"{nick} {sess.user.me} :Chatujme.cz IRC Gateway")

    # 319 RPL_WHOISCHANNELS: <nick> :<channels>
    if user_rooms:
        sess.send(sess.rfc.RPL_WHOISCHANNELS, f"{nick} :{' '.join(user_rooms)}")

    # 378 RPL_WHOISHOST: <nick> :is connecting from <host>
    sess.send(sess.rfc.RPL_WHOISHOST, f"{nick} :is connecting from {user_sex}.chatujme.cz")

    # Check if user has any special status
    has_op = any(s.get('isOP') or s.get('isOwner') for s in user_status.values())
    if has_op:
        # 313 RPL_WHOISOPERATOR (custom usage)
        sess.send_raw(f":{sess.user.me} 313 {sess.user.nick} {nick} :is a room operator\r\n")

    # 318 RPL_ENDOFWHOIS
    sess.send(sess.rfc.RPL_ENDOFWHOIS, f"{nick} :End of /WHOIS list")
