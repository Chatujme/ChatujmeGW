import unittest
from unittest import mock

from chatujmegw import config

from .helpers import add_room, make_inst, make_logged_inst, sent


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
        with mock.patch.object(inst.api, 'post', return_value=response):
            inst.parse("NICK test2\r\n", 0)
            inst.parse("USER test2 0 * :test2\r\n", 0)
            inst.parse("PASS heslo\r\n", 0)
        return inst

    def test_login_success_sends_welcome(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        self.assertTrue(inst.user.login)
        self.assertIn(" 001 ", sent(inst))
        self.assertIn("End of /MOTD", sent(inst))

    def test_login_bad_credentials_relayed(self):
        inst = self.login('{"id": null, "code": 401, "message": "Spatne heslo"}')
        self.assertFalse(inst.user.login)
        self.assertIn(" 444 ", sent(inst))
        self.assertIn("Spatne heslo", sent(inst))

    def test_login_2fa_message_relayed(self):
        # Regression: 403 (2FA required) used to fail silently
        inst = self.login('{"id": null, "code": 403, "message": "Two-factor authentication required."}')
        self.assertFalse(inst.user.login)
        self.assertIn("Two-factor authentication required.", sent(inst))

    def test_relogin_is_silent(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        inst.socket.sent = b''
        with mock.patch.object(inst.api, 'post',
                               return_value='{"id": 1, "username": "test2", "code": 200, "message": "success"}'):
            self.assertTrue(inst.relogin())
        self.assertNotIn(" 001 ", sent(inst))  # no duplicate welcome
        self.assertIn("Re-login successful", sent(inst))

    def test_relogin_failure_notifies_and_backs_off(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        inst.socket.sent = b''
        with mock.patch.object(inst.api, 'post', return_value='{"id": null, "code": 401, "message": "x"}'), \
                mock.patch('time.sleep') as slept:
            self.assertFalse(inst.relogin())
            slept.assert_called_once_with(10)
        self.assertIn("Re-login failed", sent(inst))


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


class TestRooms(unittest.TestCase):
    def test_is_in_room_garbage(self):
        inst = make_inst()
        add_room(inst, 15)
        self.assertFalse(inst.is_in_room("abc"))
        self.assertFalse(inst.is_in_room(None))
        self.assertTrue(inst.is_in_room("15"))
        self.assertTrue(inst.is_in_room(15))

    def test_send_text_urlencodes_params(self):
        # Regression: roomId/target were interpolated raw into POST body
        inst = make_logged_inst()
        captured = {}
        with mock.patch.object(inst.api, 'post',
                               side_effect=lambda url, postdata: captured.update(pd=postdata) or '{}'):
            inst.send_text("text s diakritikou ěšč", "15", "#15&admin=1")
        self.assertIn("roomId=15", captured['pd'])
        self.assertNotIn("&admin=1", captured['pd'])
        self.assertIn("%2315%26admin%3D1", captured['pd'])


class TestSendRaw(unittest.TestCase):
    def test_long_line_truncated(self):
        inst = make_inst()
        inst.send_raw("A" * 1000 + "\r\n")
        payload = inst.socket.sent.decode('utf-8')
        self.assertLessEqual(len(payload), config.MAX_LINE_LENGTH + 2)
        self.assertTrue(payload.endswith("\r\n"))
