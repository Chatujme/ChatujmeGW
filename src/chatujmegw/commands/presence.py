"""Client preference commands: AWAY, QUIT, IDLER and SMILES display mode."""

import time

from .. import numerics, textfilters

SMILES_MODES = {
    "HIDE": textfilters.SMILES_HIDE,
    "TEXT": textfilters.SMILES_TEXT,
    "URL": textfilters.SMILES_URL,
    "CODE": textfilters.SMILES_CODE,
}


def smiles(sess, parts, line):
    # SMILES [HIDE|TEXT|URL|CODE|STATUS] - how incoming smileys are rendered
    subcmd = parts[1].upper() if len(parts) > 1 else "STATUS"
    if subcmd in SMILES_MODES:
        sess.user.show_smiles = SMILES_MODES[subcmd]
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Smileys display mode set to {subcmd}\r\n")
        return
    mode_names = {v: k for k, v in SMILES_MODES.items()}
    current = mode_names.get(sess.user.show_smiles, "TEXT")
    sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :SMILES - Smiley display mode (current: {current})\r\n")
    sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  SMILES TEXT - description when available, *ID* code otherwise (default)\r\n")
    sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  SMILES CODE - always the *ID* code (can be sent back)\r\n")
    sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  SMILES URL  - image URL\r\n")
    sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  SMILES HIDE - hide smileys\r\n")


def away(sess, parts, line):
    # AWAY [message] - set/unset away status with auto-message to rooms
    if len(parts) > 1:
        # Set away with message
        msg_start = line.find(':', 1)
        if msg_start != -1:
            away_msg = line[msg_start + 1:]
        else:
            away_msg = ' '.join(parts[1:])
        sess.user.away_message = away_msg
        sess.user.away_last_sent = time.time()
        sess.send_numeric(numerics.RPL_NOWAWAY, ":You have been marked as being away")
        # Notify all rooms (away-notify capability)
        for _channel in sess.channels:
            sess.send_raw(f":{sess.user.nick}!{sess.user.nick}@{sess.server_name} AWAY :{away_msg}\r\n")
        # Send away message to all rooms immediately
        for channel in sess.channels:
            sess.send_text(away_msg, channel.id, channel.id)
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Away message sent to all rooms (will repeat every 30 min)\r\n")
    else:
        # Unset away
        sess.user.away_message = None
        sess.user.away_last_sent = 0
        sess.send_numeric(numerics.RPL_UNAWAY, ":You are no longer marked as being away")
        # Notify all rooms (away-notify capability)
        for _channel in sess.channels:
            sess.send_raw(f":{sess.user.nick}!{sess.user.nick}@{sess.server_name} AWAY\r\n")


def quit_(sess, parts, line):
    # Leave all rooms - don't send PART back to client (they're quitting)
    for channel in sess.channels[:]:
        sess.part_channel(channel.id, notify_client=False)
    sess.parent.running = False
    sess.connection = False


def idler(sess, parts, line):
    # IDLER [ON|OFF|STATUS|TIME <seconds>|TEXT <text>]
    # Auto-send message when idle for specified time
    if len(parts) < 2:
        # Show help
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :IDLER - Auto-send message when idle\r\n")
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  IDLER ON        - Enable idler\r\n")
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  IDLER OFF       - Disable idler\r\n")
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  IDLER STATUS    - Show current settings\r\n")
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  IDLER TIME <s>  - Set idle time in seconds (default: 2400 = 40min)\r\n")
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  IDLER TEXT <t>  - Set idler message(s), comma-separated\r\n")
        return

    subcmd = parts[1].upper()
    if subcmd == "ON":
        sess.user.idler_enable = True
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Idler enabled (time: {sess.user.idler_timer}s)\r\n")
    elif subcmd == "OFF":
        sess.user.idler_enable = False
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Idler disabled\r\n")
    elif subcmd == "STATUS":
        status = "ON" if sess.user.idler_enable else "OFF"
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Idler: {status}, Time: {sess.user.idler_timer}s ({sess.user.idler_timer//60}min), Text: {sess.user.idler_text}\r\n")
        # Show per-channel idler status using server-side sayAgo
        if sess.channels:
            for channel in sess.channels:
                idle = channel.say_ago_seconds
                remaining = max(0, sess.user.idler_timer - idle)
                sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :  #{channel.id}: idle {idle}s, {remaining}s remaining ({remaining//60}min {remaining%60}s)\r\n")
    elif subcmd == "TIME" and len(parts) >= 3:
        try:
            new_time = int(parts[2])
            if new_time < 1800:  # 30 minutes minimum
                sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Minimum idler time is 1800 seconds (30 minutes)\r\n")
            else:
                sess.user.idler_timer = new_time
                sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Idler time set to {new_time}s ({new_time//60}min)\r\n")
        except ValueError:
            sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Invalid time value\r\n")
    elif subcmd == "TEXT" and len(parts) >= 3:
        text = ' '.join(parts[2:])
        sess.user.idler_text = [t.strip() for t in text.split(',')]
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Idler text set to: {sess.user.idler_text}\r\n")
    else:
        sess.send_raw(f":{sess.server_name} NOTICE {sess.user.nick} :Unknown IDLER subcommand. Use: ON, OFF, STATUS, TIME, TEXT\r\n")
