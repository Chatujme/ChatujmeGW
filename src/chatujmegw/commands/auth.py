"""Login and identity commands: CAP, NICK, USER, PASS, NickServ, REGISTER."""

from .. import config
from ..util import validate_nick

# Supported IRC capabilities
SUPPORTED_CAPS = ["away-notify"]


def cap(sess, parts, line):
    """Handle CAP negotiation for modern IRC clients"""
    if len(parts) < 2:
        return

    subcmd = parts[1].upper()
    if subcmd == "LS":
        # Send supported capabilities
        caps_str = ' '.join(SUPPORTED_CAPS)
        sess.send_raw(f":{sess.user.me} CAP * LS :{caps_str}\r\n")
        sess.cap_negotiating = True
    elif subcmd == "LIST":
        # List enabled capabilities
        caps_str = ' '.join(SUPPORTED_CAPS)
        sess.send_raw(f":{sess.user.me} CAP * LIST :{caps_str}\r\n")
    elif subcmd == "REQ":
        # Handle capability requests
        caps = ' '.join(parts[2:]).lstrip(':') if len(parts) > 2 else ""
        requested = caps.split()
        # Check if all requested caps are supported
        all_supported = all(c.lstrip('-') in SUPPORTED_CAPS for c in requested)
        if all_supported:
            sess.send_raw(f":{sess.user.me} CAP * ACK :{caps}\r\n")
        else:
            sess.send_raw(f":{sess.user.me} CAP * NAK :{caps}\r\n")
    elif subcmd == "END":
        sess.cap_negotiating = False
        # If we have credentials and not already logged in, try to login
        if not sess.user.login and sess.user.password and sess.user.nick and sess.user.username:
            sess.user.login = sess.check_login()


def nick(sess, parts, line):
    if len(parts) < 2:
        return
    if sess.user.login:
        sess.send_raw(f":{sess.user.me} NOTICE {sess.user.username} :Already logged in\r\n")
        return
    new_nick = parts[1].lstrip(':')
    # Security: Validate nickname (Chatujme.cz rules)
    if not validate_nick(new_nick):
        sess.send_raw(f":{sess.user.me} NOTICE * :Invalid nickname ({config.MIN_NICK_LENGTH}-{config.MAX_NICK_LENGTH} chars, a-z 0-9 - _ only, must start with letter)\r\n")
        return
    sess.user.nick = new_nick
    if sess.user.password and sess.user.username:
        sess.user.login = sess.check_login()


def user(sess, parts, line):
    if len(parts) < 2:
        return
    if sess.user.login:
        return
    sess.user.username = parts[1]
    if sess.user.password and sess.user.nick:
        sess.user.login = sess.check_login()


def password(sess, parts, line):
    if len(parts) < 2:
        return
    if sess.user.login:
        return
    # Take the whole rest of the line (passwords may contain spaces),
    # strip the optional RFC trailing-parameter colon
    value = line.split(" ", 1)[1]
    if value.startswith(':'):
        value = value[1:]
    sess.user.password = value
    if sess.user.username and sess.user.nick:
        sess.user.login = sess.check_login()


def nickserv(sess, parts, line):
    # Handle NICKSERV/NS shortcut commands (some clients send these)
    if len(parts) > 1:
        subcmd = parts[1].lower()
        if subcmd == "identify" or subcmd == "id":
            # Already logged in via PASS, ignore
            sess.send_raw(f":NickServ!services@{sess.user.me} NOTICE {sess.user.nick} :You are already identified.\r\n")
        elif subcmd == "register":
            sess.send_raw(f":NickServ!services@{sess.user.me} NOTICE {sess.user.nick} :Registration is only available via web.\r\n")
            sess.send_raw(f":NickServ!services@{sess.user.me} NOTICE {sess.user.nick} :Please visit: https://chatujme.cz/registrace\r\n")
        else:
            sess.send_raw(f":NickServ!services@{sess.user.me} NOTICE {sess.user.nick} :Unknown NickServ command: {subcmd}\r\n")


def register(sess, parts, line):
    # Direct REGISTER command - redirect to web
    sess.send_raw(f":{sess.user.me} NOTICE * :Registration is only available via web.\r\n")
    sess.send_raw(f":{sess.user.me} NOTICE * :Please visit: https://chatujme.cz/registrace\r\n")
