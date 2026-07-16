"""Command dispatch: maps IRC commands to handler functions.

Handlers share the signature handler(sess, parts, line) where sess is the
owning Chatujme session, parts the space-split line and line the raw line.
"""

from . import auth, info, messaging, presence, rooms

HANDLERS = {
    "CAP": auth.cap,
    "NICK": auth.nick,
    "USER": auth.user,
    "PASS": auth.password,
    "NICKSERV": auth.nickserv,
    "NS": auth.nickserv,
    "REGISTER": auth.register,
    "JOIN": rooms.join,
    "PART": rooms.part,
    "LIST": rooms.list_rooms,
    "NAMES": rooms.names,
    "WHO": rooms.who,
    "MODE": rooms.mode,
    "TOPIC": rooms.topic,
    "KICK": rooms.kick,
    "PRIVMSG": messaging.privmsg,
    "NOTICE": messaging.privmsg,
    "WHOIS": info.whois,
    "USERHOST": info.userhost,
    "VERSION": info.version,
    "MOTD": info.motd,
    "PING": info.ping,
    "PONG": info.pong,
    "AWAY": presence.away,
    "QUIT": presence.quit_,
    "IDLER": presence.idler,
    "SMILES": presence.smiles,
}

# Commands that hit the Chatujme API and are subject to the per-connection rate limit
RATE_LIMITED = frozenset({"LIST", "WHO", "WHOIS", "NAMES", "TOPIC"})
