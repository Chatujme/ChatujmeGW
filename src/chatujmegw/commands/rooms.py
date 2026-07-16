"""Room commands: JOIN, PART, LIST, NAMES, WHO, MODE, TOPIC, KICK."""

import json
import traceback as tb

from .. import config, numerics
from ..util import log, validate_room_id


def join(sess, parts, line):
    if len(parts) < 2:
        return
    if not sess.user.login:
        sess.send_numeric(numerics.ERR_NOLOGIN, f"{sess.server_name} :Not logged in")
        return

    for channel_id in parts[1].replace('#', '').split(','):
        # Security: Validate room ID before joining
        if not validate_room_id(channel_id):
            sess.send_numeric(numerics.ERR_NOSUCHCHANNEL, f"#{channel_id} :Invalid room ID")
            continue
        sess.join_channel(channel_id)


def part(sess, parts, line):
    if len(parts) < 2:
        sess.send_numeric(numerics.ERR_NEEDMOREPARAMS, "PART :Not enough parameters")
        return
    room_id = parts[1].lstrip('#')
    if not validate_room_id(room_id):
        sess.send_numeric(numerics.ERR_NOSUCHCHANNEL, f"#{room_id} :Invalid room ID")
        return
    sess.part_channel(room_id)


def list_rooms(sess, parts, line):
    rooms = json.loads(sess.api.get_rooms())
    sess.send_numeric(numerics.RPL_LISTSTART, "Channel :Users Name")
    if isinstance(rooms, list):
        for room in rooms:
            sess.send_numeric(numerics.RPL_LIST, f"#{room['id']} {room['online']} :{sess.field(room['nazev'])}")
    sess.send_numeric(numerics.RPL_LISTEND, ":End of /LIST")


def names(sess, parts, line):
    if len(parts) >= 2:
        room_id = parts[1].lstrip('#')
        if sess.in_channel(room_id):
            sess.send_names(room_id)
        else:
            sess.send_numeric(numerics.ERR_NOSUCHCHANNEL, f"#{room_id} :No such channel")


def who(sess, parts, line):
    if len(parts) >= 2:
        room_id = parts[1].lstrip('#')
        if room_id.isdigit():
            users = sess.fetch_channel_members(room_id)
            for user in users:
                nick = sess.field(user['nick'])
                sex = sess.field(user['sex'])
                sess.send_numeric(
                    numerics.RPL_WHOREPLY,
                    f"#{room_id} {nick} {sex} {sess.server_name} {nick} H :0 {nick}"
                )
        sess.send_numeric(numerics.RPL_ENDOFWHO, ":End of /WHO list")


def mode(sess, parts, line):
    if len(parts) >= 2:
        target = parts[1]
        # MODE #room +o nick - give operator to nick (predej spravce)
        if len(parts) >= 4 and parts[2] in ('+o', '+O'):
            room_id = target.lstrip('#')
            nick = parts[3]
            sess.send_text(f"/predej {nick}", room_id, room_id)
            sess.send_raw(f":{sess.server_name} MODE #{room_id} +o {nick}\r\n")
        # MODE #room +b - list bans (not supported, return empty)
        elif len(parts) >= 3 and parts[2] == '+b':
            room_id = target.lstrip('#')
            sess.send_raw(f":{sess.server_name} 368 {sess.user.nick} #{room_id} :End of channel ban list\r\n")
        else:
            # Just return channel modes
            sess.send_numeric(numerics.RPL_CHANNELMODEIS, f"{target} +tn")


def _show_topic(sess, room_id):
    try:
        response = sess.api.get_room(room_id)
        data = json.loads(response)
        sess.send_numeric(numerics.RPL_TOPIC, f"#{data['id']} :[{sess.field(data['nazev'])}] {sess.field(data['topic'])}")
    except Exception:
        if config.DEBUG:
            tb.print_exc()


def topic(sess, parts, line):
    if len(parts) < 2:
        return
    room_id = parts[1].lstrip('#')
    # Check if setting new topic or just viewing
    if len(parts) >= 3:
        new_topic = ' '.join(parts[2:]).lstrip(':')
        if new_topic:
            try:
                response = sess.api.set_topic(room_id, new_topic)
                data = json.loads(response)
                if data.get('code') == 200:
                    # Success - send topic change notification
                    sess.send_raw(
                        f":{sess.make_hostmask(sess.user.username, room_id)} TOPIC #{room_id} :{new_topic}\r\n"
                    )
                elif data.get('code') == 403:
                    # No permission
                    sess.send_raw(f":{sess.server_name} 482 {sess.user.nick} #{room_id} :{data.get('message', 'Permission denied')}\r\n")
                else:
                    sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Error: {data.get('message', 'Unknown error')}\r\n")
            except Exception as e:
                if config.DEBUG:
                    tb.print_exc()
                sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Error changing topic: {e}\r\n")
        else:
            # Empty topic = show current
            _show_topic(sess, room_id)
    else:
        # Just viewing topic
        _show_topic(sess, room_id)


def kick(sess, parts, line):
    if len(parts) >= 3:
        room_id = parts[1].lstrip('#')
        nick = parts[2]
        reason = ' '.join(parts[3:]).lstrip(':') if len(parts) > 3 else ""
        sess.send_text(f"/kick {nick} {reason}".strip(), room_id, room_id)
        sess.send_raw(
            f":{sess.make_hostmask(sess.user.username, room_id)} KICK #{room_id} {nick} :{reason}\r\n"
        )
    else:
        sess.send_numeric(numerics.ERR_NEEDMOREPARAMS, "KICK :Not enough parameters")
