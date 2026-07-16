"""Logging, sanitization and input validation helpers."""

import re
import sys
import time

from . import config


def log(text, sanitize=True):
    """Log message with optional sanitization of sensitive data"""
    text = str(text).replace("\r", "").replace("\n", " | ")
    if sanitize:
        text = sanitize_log(text)
    print(f"[{time.strftime('%Y/%m/%d %H:%M:%S')}] {text}", flush=True)


def fatal_error_pause():
    """On Windows, pause before exit so user can read error messages"""
    if sys.platform == 'win32':
        log("Closing in 10 seconds...")
        time.sleep(10)


def sanitize_irc(text):
    """
    Remove CRLF, null bytes and other control characters from IRC messages.
    Prevents IRC protocol injection attacks.
    Preserves \x01 (CTCP marker) and \t (tab).
    """
    if not text:
        return ""
    # Remove CR, LF, null bytes
    text = text.replace('\r', '').replace('\n', '').replace('\0', '')
    # Remove other potentially dangerous control characters (0x00-0x1F except CTCP marker and tab)
    return ''.join(c for c in text if ord(c) >= 32 or c == '\t' or c == '\x01')


def sanitize_log(text):
    """
    Sanitize sensitive data from log output.
    Masks passwords, tokens, and session data.
    """
    if not text:
        return ""
    text = str(text)
    # Mask password in various formats
    text = re.sub(r'password=[^&\s"\']+', 'password=***', text, flags=re.IGNORECASE)
    text = re.sub(r'"password"\s*:\s*"[^"]+"', '"password": "***"', text, flags=re.IGNORECASE)
    text = re.sub(r'PASS\s+\S+', 'PASS ***', text, flags=re.IGNORECASE)
    # Mask tokens and session IDs
    text = re.sub(r'token=[^&\s"\']+', 'token=***', text, flags=re.IGNORECASE)
    text = re.sub(r'session[_-]?id=[^&\s"\']+', 'session_id=***', text, flags=re.IGNORECASE)
    text = re.sub(r'cookie:\s*[^\r\n]+', 'cookie: ***', text, flags=re.IGNORECASE)
    return text


def validate_nick(nick):
    """
    Validate nickname according to Chatujme.cz rules:
    - Only a-z, 0-9, dash (-), underscore (_)
    - Must NOT start with number, dash, underscore, or dot
    - Length: 4-23 characters
    """
    if not nick:
        return False
    if len(nick) < config.MIN_NICK_LENGTH or len(nick) > config.MAX_NICK_LENGTH:
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
        return 0 < rid <= config.MAX_ROOM_ID
    except (ValueError, TypeError):
        return False
