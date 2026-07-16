import unittest
from unittest import mock

from chatujmegw import config

from .helpers import add_channel, make_inst, make_logged_inst, sent


class TestPassParsing(unittest.TestCase):
    def test_pass_plain(self):
        inst = make_inst()
        inst.feed("PASS heslo123\r\n")
        self.assertEqual(inst.user.password, "heslo123")

    def test_pass_with_colon_prefix(self):
        inst = make_inst()
        inst.feed("PASS :heslo123\r\n")
        self.assertEqual(inst.user.password, "heslo123")

    def test_pass_with_spaces(self):
        inst = make_inst()
        inst.feed("PASS :my secret pass\r\n")
        self.assertEqual(inst.user.password, "my secret pass")


class TestLogin(unittest.TestCase):
    def login(self, response):
        inst = make_inst()
        with mock.patch.object(inst.api, 'post', return_value=response):
            inst.feed("NICK test2\r\n")
            inst.feed("USER test2 0 * :test2\r\n")
            inst.feed("PASS heslo\r\n")
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

    def test_reauthenticate_is_silent(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        inst.socket.sent = b''
        with mock.patch.object(inst.api, 'post',
                               return_value='{"id": 1, "username": "test2", "code": 200, "message": "success"}'):
            self.assertTrue(inst.reauthenticate())
        self.assertNotIn(" 001 ", sent(inst))  # no duplicate welcome
        self.assertIn("Re-login successful", sent(inst))

    def test_reauthenticate_failure_notifies_and_backs_off(self):
        inst = self.login('{"id": 1, "username": "test2", "code": 200, "message": "success"}')
        inst.socket.sent = b''
        with mock.patch.object(inst.api, 'post', return_value='{"id": null, "code": 401, "message": "x"}'), \
                mock.patch('time.sleep') as slept:
            self.assertFalse(inst.reauthenticate())
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
        inst.feed(f"PONG :{inst.user.pending_ping_token}\r\n")
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
    def test_find_channel_garbage(self):
        inst = make_inst()
        add_channel(inst, 15)
        self.assertFalse(inst.in_channel("abc"))
        self.assertFalse(inst.in_channel(None))
        self.assertTrue(inst.in_channel("15"))
        self.assertTrue(inst.in_channel(15))

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


class TestGhost(unittest.TestCase):
    def test_ghost_clears_old_session_channels(self):
        # Regression: rename refactor left `old.instance.rooms = []` so the old
        # session's cleanup would part the channels the new session just joined
        from chatujmegw import state
        old = make_logged_inst()
        add_channel(old, 15)
        new = make_logged_inst()
        state.active_connections['test2'] = old.parent
        try:
            with mock.patch.object(new.api, 'join',
                                   return_value={'code': 200, 'id': 15, 'nazev': 'X', 'topic': 'T'}), \
                    mock.patch.object(new.api, 'get_users', return_value='[]'):
                new.join_channel('15')
            self.assertEqual(old.channels, [])
            self.assertFalse(old.parent.running)
            self.assertIs(state.active_connections['test2'], new.parent)
        finally:
            state.active_connections.pop('test2', None)


class TestSendRaw(unittest.TestCase):
    def test_long_line_truncated_by_bytes(self):
        inst = make_inst()
        inst.send_raw("A" * 1000 + "\r\n")
        self.assertLessEqual(len(inst.socket.sent), config.MAX_LINE_LENGTH)
        self.assertTrue(inst.socket.sent.endswith(b"\r\n"))

    def test_multibyte_line_capped_on_bytes_not_chars(self):
        # Regression: cap was char-based, so 510 'é' framed 1022 bytes
        inst = make_inst()
        inst.send_raw("é" * 400 + "\r\n")
        self.assertLessEqual(len(inst.socket.sent), config.MAX_LINE_LENGTH)
        # no split multibyte char survived the truncation
        inst.socket.sent.decode('utf-8')  # must not raise
        self.assertTrue(inst.socket.sent.endswith(b"\r\n"))


class TestAuthRateLimit(unittest.TestCase):
    def test_login_attempts_rate_limited(self):
        # Regression: unthrottled PASS retries could brute-force / amplify to API
        inst = make_inst()
        inst.user.nick = "test2"
        inst.user.username = "test2"
        inst.user.password = "x"
        inst.user.command_timestamps = [1e12] * config.MAX_COMMANDS_PER_SECOND
        with mock.patch('time.time', return_value=1e12), \
                mock.patch.object(inst.api, 'authenticate') as api:
            self.assertFalse(inst.authenticate())
            api.assert_not_called()
        self.assertIn("Too many login attempts", sent(inst))


class TestFetchMembers(unittest.TestCase):
    def test_error_object_does_not_crash(self):
        # Regression: get-users returns {code:500} on failure, not a list -
        # the poller must not die on it
        inst = make_logged_inst()
        add_channel(inst, 15)
        with mock.patch.object(inst.api, 'get_users',
                               return_value='{"code": 500, "message": "boom"}'):
            self.assertEqual(inst.fetch_channel_members(15), [])
        self.assertEqual(inst.find_channel(15).members, [])
