"""Active security regression tests (no network - drive the parser/socket directly).

These encode the abuse cases from the security audit so a future refactor can't
silently reintroduce an injection or resource-exhaustion hole.
"""

import unittest
from unittest import mock

from chatujmegw import config, state
from chatujmegw.util import sanitize_irc

from .helpers import add_channel, make_inst, make_logged_inst, sent


class TestClientInjection(unittest.TestCase):
    def test_privmsg_crlf_stripped_before_api(self):
        # A client trying to smuggle a second command through message text
        inst = make_logged_inst()
        add_channel(inst, 15)
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed("PRIVMSG #15 :hi\r\nQUIT\r\n")
            # QUIT on its own line is a separate command, but text with embedded
            # control chars must be sanitized when present in one logical line
            sent_texts = [c.args[0] for c in st.call_args_list]
        self.assertTrue(all("\n" not in t and "\r" not in t for t in sent_texts))

    def test_oversized_line_truncated_not_crashed(self):
        inst = make_logged_inst()
        inst.feed("PRIVMSG #15 :" + "A" * 5000 + "\r\n")  # must not raise
        self.assertTrue(inst.connection)

    def test_null_bytes_in_command_do_not_crash(self):
        inst = make_inst()
        inst.feed("NICK te\x00st2\r\n")  # must not raise

    def test_unknown_binary_garbage(self):
        inst = make_inst()
        inst.feed("\x01\x02\x03 garbage \xff\r\n")  # must not raise


class TestRoomIdValidation(unittest.TestCase):
    def test_join_rejects_url_injection_in_room_id(self):
        # room id goes into an API URL - only digits may pass validation
        inst = make_logged_inst()
        # single-token payloads (IRC splits on space, so the id is the first token)
        with mock.patch.object(inst, 'join_channel') as jc:
            for bad in ("15&admin=1", "../part", "%2e%2e", "1e9", "0x0f", "15;rm"):
                inst.feed(f"JOIN #{bad}\r\n")
            jc.assert_not_called()

    def test_part_rejects_nonnumeric(self):
        inst = make_logged_inst()
        add_channel(inst, 15)
        inst.feed("PART #../../etc\r\n")
        self.assertIn(" 403 ", sent(inst))


class TestCredentialSafety(unittest.TestCase):
    def test_password_not_in_debug_log(self):
        from chatujmegw.util import sanitize_log
        self.assertNotIn("hunter2", sanitize_log("PASS hunter2"))
        self.assertNotIn("hunter2", sanitize_log("username=x&password=hunter2"))

    def test_pass_command_masked_regardless_of_case(self):
        from chatujmegw.util import sanitize_log
        self.assertNotIn("s3cr3t", sanitize_log("pass s3cr3t"))


class TestDoS(unittest.TestCase):
    def test_per_ip_connection_rate_limit(self):
        ip = "203.0.113.44"
        state.connection_log.pop(ip, None)
        allowed = sum(1 for _ in range(20) if state.allow_connection(ip))
        self.assertEqual(allowed, config.MAX_CONNECTIONS_PER_IP)

    def test_command_flood_rate_limited(self):
        inst = make_logged_inst()
        inst.user.command_timestamps = [1e12] * config.MAX_COMMANDS_PER_SECOND
        with mock.patch('time.time', return_value=1e12), \
                mock.patch.object(inst.api, 'get_rooms') as api:
            inst.feed("LIST\r\n")
            api.assert_not_called()

    def test_system_message_regex_no_catastrophic_backtrack(self):
        # ReDoS guard: handle_system_message caps input length; a crafted string
        # must return quickly, not hang
        import time as _time
        from chatujmegw.poller import MessagePoller
        inst = make_logged_inst()
        ch = add_channel(inst, 15)
        poller = MessagePoller(inst)
        evil = "Uživatel " + "a " * 300 + "byl vykopnut z místnosti."
        start = _time.time()
        poller.handle_system_message(evil, ch)  # must not hang
        self.assertLess(_time.time() - start, 2.0)


class TestGhostIdentity(unittest.TestCase):
    def test_login_binds_to_api_username_not_client_nick(self):
        # H1: client sets someone else's NICK but logs in as itself -> the session
        # must be keyed to the API-confirmed username, not the spoofed nick
        inst = make_inst()
        with mock.patch.object(inst.api, 'authenticate',
                               return_value='{"id": 1, "username": "attacker", "code": 200}'):
            inst.feed("NICK victim\r\n")
            inst.feed("USER attacker 0 * :x\r\n")
            inst.feed("PASS pw\r\n")
        self.assertTrue(inst.user.login)
        self.assertEqual(inst.user.nick, "attacker")  # not "victim"


class TestSyncPathSanitize(unittest.TestCase):
    def test_list_room_name_crlf_stripped(self):
        # M3: a room name with CRLF must not inject an IRC line into LIST output
        inst = make_logged_inst()
        with mock.patch.object(inst.api, 'get_rooms',
                               return_value='[{"id": 15, "online": 1, "nazev": "evil\\r\\nKICK #1 x"}]'):
            inst.feed("LIST\r\n")
        out = sent(inst)
        self.assertNotIn("\nKICK", out)
        self.assertIn("evilKICK #1 x", out)  # collapsed onto one safe line

    def test_topic_crlf_stripped(self):
        inst = make_logged_inst()
        with mock.patch.object(inst.api, 'get_room',
                               return_value='{"id": 15, "nazev": "R", "topic": "hi\\r\\nNOTICE evil :x"}'):
            inst.feed("TOPIC #15\r\n")
        self.assertNotIn("\nNOTICE", sent(inst))

    def test_list_non_list_response_does_not_disconnect(self):
        # L5: malformed API response inside a handler is contained, not fatal
        inst = make_logged_inst()
        with mock.patch.object(inst.api, 'get_rooms', return_value='{"code": 500}'):
            inst.feed("LIST\r\n")  # must not raise
        self.assertTrue(inst.connection)


class TestLogMasking(unittest.TestCase):
    def test_pass_with_spaces_fully_masked(self):
        from chatujmegw.util import sanitize_log
        out = sanitize_log("PASS my secret pass phrase")
        self.assertNotIn("secret", out)
        self.assertNotIn("phrase", out)

    def test_json_session_token_masked(self):
        from chatujmegw.util import sanitize_log
        self.assertNotIn("abc123", sanitize_log('{"sessionId": "abc123"}'))


class TestSanitizerProperties(unittest.TestCase):
    def test_sanitize_removes_all_line_breaks(self):
        for payload in ("a\rb", "a\nb", "a\r\nb", "a\n\rb", "a\x00b"):
            out = sanitize_irc(payload)
            self.assertNotIn("\r", out)
            self.assertNotIn("\n", out)
            self.assertNotIn("\x00", out)

    def test_sanitize_preserves_ctcp_and_utf8(self):
        self.assertEqual(sanitize_irc("\x01ACTION žluťoučký\x01"), "\x01ACTION žluťoučký\x01")


if __name__ == '__main__':
    unittest.main()
