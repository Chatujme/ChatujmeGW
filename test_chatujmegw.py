#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for ChatujmeGW. Stdlib only, no network.

Run: python -m unittest test_chatujmegw -v
"""

import unittest
from unittest import mock

from gw import config
from gw.client import Chatujme
from gw.models import RoomStruct
from gw.util import sanitize_irc, sanitize_log, validate_nick, validate_room_id


class FakeSocket:
    def __init__(self):
        self.sent = b''

    def sendall(self, data):
        self.sent += data

    def send(self, data):
        self.sent += data
        return len(data)

    def lines(self):
        return self.sent.decode('utf-8').split('\r\n')


class FakeHandler:
    def __init__(self):
        self.running = True
        self.instance = None
        self.socket = FakeSocket()


def make_inst():
    handler = FakeHandler()
    inst = Chatujme(FakeSocket(), '127.0.0.1', handler)
    handler.instance = inst
    return inst


class TestSanitizers(unittest.TestCase):
    def test_sanitize_irc_strips_crlf_and_null(self):
        self.assertEqual(sanitize_irc("a\r\nb\0c"), "abc")

    def test_sanitize_irc_keeps_ctcp_and_tab(self):
        self.assertEqual(sanitize_irc("\x01ACTION hi\x01\tx"), "\x01ACTION hi\x01\tx")

    def test_sanitize_irc_strips_control_chars(self):
        self.assertEqual(sanitize_irc("a\x02b\x1fc"), "abc")

    def test_sanitize_log_masks_password(self):
        self.assertIn("password=***", sanitize_log("username=x&password=tajne123"))
        self.assertNotIn("tajne123", sanitize_log("username=x&password=tajne123"))

    def test_sanitize_log_masks_pass_command(self):
        self.assertNotIn("secret", sanitize_log("PASS secret"))

    def test_sanitize_log_masks_tokens(self):
        self.assertNotIn("abc123", sanitize_log("token=abc123"))


class TestValidators(unittest.TestCase):
    def test_valid_nicks(self):
        for nick in ("test2", "LuRy-x", "abcd", "a" * 23):
            self.assertTrue(validate_nick(nick), nick)

    def test_invalid_nicks(self):
        for nick in ("", "abc", "a" * 24, "1abc", "-abc", "_abc", "ab.cd", "ab cd", "háčky"):
            self.assertFalse(validate_nick(nick), nick)

    def test_room_id(self):
        self.assertTrue(validate_room_id("15"))
        self.assertTrue(validate_room_id(999999))
        for bad in ("abc", "", None, "0", "-1", "1000000", "12&x=1"):
            self.assertFalse(validate_room_id(bad), repr(bad))


class TestCleaners(unittest.TestCase):
    def setUp(self):
        self.inst = make_inst()

    def test_clean_urls_unwraps_redirect(self):
        msg = '<a href="//link.chatujme.cz/redirect?url=https%3A%2F%2Fexample.com%2Fx" target="_blank">example</a>'
        self.assertEqual(self.inst.clean_urls(msg), "https://example.com/x")

    def test_clean_urls_protocol_relative(self):
        msg = '<a href="//example.com/a" target="_blank">a</a>'
        self.assertEqual(self.inst.clean_urls(msg), "https://example.com/a")

    def test_clean_smiles_text_mode(self):
        self.inst.user.show_smiles = 1
        msg = "<img src='https://static.chatujme.cz/smiles/42.gif' alt=':)' aria-label='x' title='x'>"
        self.assertEqual(self.inst.clean_smiles(msg), "*42*")

    def test_clean_highlight(self):
        msg = "<span style='background:#eded1a'>test2</span>: ahoj"
        self.assertEqual(self.inst.clean_highlight(msg), "test2: ahoj")


class TestPassParsing(unittest.TestCase):
    def test_pass_plain(self):
        inst = make_inst()
        inst.parse("PASS heslo123\r\n", 0)
        self.assertEqual(inst.user.password, "heslo123")

    def test_pass_with_colon_prefix(self):
        inst = make_inst()
        inst.parse("PASS :heslo123\r\n", 0)
        self.assertEqual(inst.user.password, "heslo123")

    def test_pass_with_spaces(self):
        inst = make_inst()
        inst.parse("PASS :my secret pass\r\n", 0)
        self.assertEqual(inst.user.password, "my secret pass")


class TestLogin(unittest.TestCase):
    def login(self, response):
        inst = make_inst()
        with mock.patch.object(inst, 'post_url', return_value=response):
            inst.parse("NICK test2\r\n", 0)
            inst.parse("USER test2 0 * :test2\r\n", 0)
            inst.parse("PASS heslo\r\n", 0)
        return inst

    def test_login_success_sends_welcome(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        self.assertTrue(inst.user.login)
        out = inst.socket.sent.decode('utf-8')
        self.assertIn(" 001 ", out)
        self.assertIn("End of /MOTD", out)

    def test_login_bad_credentials_relayed(self):
        inst = self.login('{"id": null, "code": 401, "message": "Spatne heslo"}')
        self.assertFalse(inst.user.login)
        out = inst.socket.sent.decode('utf-8')
        self.assertIn(" 444 ", out)
        self.assertIn("Spatne heslo", out)

    def test_login_2fa_message_relayed(self):
        # Regression: 403 (2FA required) used to fail silently
        inst = self.login('{"id": null, "code": 403, "message": "Two-factor authentication required."}')
        self.assertFalse(inst.user.login)
        out = inst.socket.sent.decode('utf-8')
        self.assertIn("Two-factor authentication required.", out)

    def test_relogin_is_silent(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        inst.socket.sent = b''
        with mock.patch.object(inst, 'post_url',
                               return_value='{"id": 1, "username": "test2", "code": 200, "message": "success"}'):
            self.assertTrue(inst.relogin())
        out = inst.socket.sent.decode('utf-8')
        self.assertNotIn(" 001 ", out)  # no duplicate welcome
        self.assertIn("Re-login successful", out)


class TestPartCrash(unittest.TestCase):
    def test_part_nonnumeric_does_not_kill_connection(self):
        # Regression: PART #abc raised ValueError in is_in_room -> disconnect
        inst = make_inst()
        inst.user.login = True
        inst.user.nick = "test2"
        room = RoomStruct()
        room.id = 15
        inst.rooms.append(room)
        inst.parse("PART #abc\r\n", 0)  # must not raise
        out = inst.socket.sent.decode('utf-8')
        self.assertIn(" 403 ", out)
        self.assertEqual(len(inst.rooms), 1)  # room untouched

    def test_names_nonnumeric_does_not_raise(self):
        inst = make_inst()
        inst.user.login = True
        room = RoomStruct()
        room.id = 15
        inst.rooms.append(room)
        inst.parse("NAMES #abc\r\n", 0)  # must not raise
        self.assertIn(" 403 ", inst.socket.sent.decode('utf-8'))

    def test_is_in_room_garbage(self):
        inst = make_inst()
        room = RoomStruct()
        room.id = 15
        inst.rooms.append(room)
        self.assertFalse(inst.is_in_room("abc"))
        self.assertFalse(inst.is_in_room(None))
        self.assertTrue(inst.is_in_room("15"))


class TestPrivmsg(unittest.TestCase):
    def test_pm_without_room_sends_error(self):
        # Regression: PM with no joined room was silently dropped
        inst = make_inst()
        inst.user.login = True
        inst.user.nick = "test2"
        with mock.patch.object(inst, 'send_text') as sent:
            inst.parse("PRIVMSG someone :ahoj\r\n", 0)
            sent.assert_not_called()
        out = inst.socket.sent.decode('utf-8')
        self.assertIn(" 401 ", out)
        self.assertIn("join a room first", out)

    def test_send_text_urlencodes_params(self):
        # Regression: roomId/target were interpolated raw into POST body
        inst = make_inst()
        captured = {}
        with mock.patch.object(inst, 'post_url',
                               side_effect=lambda url, postdata: captured.update(pd=postdata) or '{}'):
            inst.send_text("text s diakritikou ěšč", "15", "#15&admin=1")
        self.assertIn("roomId=15", captured['pd'])
        self.assertNotIn("&admin=1", captured['pd'])
        self.assertIn("%2315%26admin%3D1", captured['pd'])


class TestPingKeepalive(unittest.TestCase):
    def test_pong_timeout_fires(self):
        # Regression: timeout check was unreachable (timer reset every interval)
        inst = make_inst()
        self.assertTrue(inst.ping_keepalive(1000))  # sends first PING
        self.assertIsNotNone(inst.user.pending_ping_token)
        self.assertTrue(inst.ping_keepalive(1060))   # pending, within deadline
        self.assertTrue(inst.ping_keepalive(1119))   # still within deadline
        self.assertFalse(inst.ping_keepalive(1120))  # 120s without PONG -> drop

    def test_pong_resets_cycle(self):
        inst = make_inst()
        inst.ping_keepalive(1000)
        inst.parse(f"PONG :{inst.user.pending_ping_token}\r\n", 0)
        self.assertIsNone(inst.user.pending_ping_token)
        self.assertTrue(inst.ping_keepalive(1120))  # no pending PING -> alive, sends next
        self.assertIsNotNone(inst.user.pending_ping_token)

    def test_no_duplicate_ping_while_pending(self):
        inst = make_inst()
        inst.ping_keepalive(1000)
        token = inst.user.pending_ping_token
        inst.ping_keepalive(1070)
        self.assertEqual(inst.user.pending_ping_token, token)


class TestVersion(unittest.TestCase):
    # Regression: module split once rewrote "VERSION" string literals to "config.VERSION"
    def test_version_command(self):
        inst = make_inst()
        inst.user.login = True
        inst.user.nick = "test2"
        inst.parse("VERSION\r\n", 0)
        self.assertIn(" 351 ", inst.socket.sent.decode('utf-8'))

    def test_ctcp_version_request(self):
        inst = make_inst()
        inst.user.login = True
        inst.user.nick = "test2"
        inst.parse("PRIVMSG test2 :\x01VERSION\x01\r\n", 0)
        self.assertIn("\x01VERSION ChatujmeGW", inst.socket.sent.decode('utf-8'))


class TestRateLimit(unittest.TestCase):
    def test_api_commands_rate_limited(self):
        inst = make_inst()
        inst.user.login = True
        inst.user.nick = "test2"
        inst.user.command_timestamps = [1e12] * config.MAX_COMMANDS_PER_SECOND
        with mock.patch('time.time', return_value=1e12), \
                mock.patch.object(inst.system, 'get_rooms') as api:
            inst.parse("LIST\r\n", 0)
            api.assert_not_called()
        self.assertIn("Rate limit exceeded", inst.socket.sent.decode('utf-8'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
