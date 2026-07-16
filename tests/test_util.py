import unittest

from chatujmegw.util import sanitize_irc, sanitize_log, validate_nick, validate_room_id


class TestSanitizers(unittest.TestCase):
    def test_sanitize_irc_strips_crlf_and_null(self):
        self.assertEqual(sanitize_irc("a\r\nb\0c"), "abc")

    def test_sanitize_irc_keeps_ctcp_and_tab(self):
        self.assertEqual(sanitize_irc("\x01ACTION hi\x01\tx"), "\x01ACTION hi\x01\tx")

    def test_sanitize_irc_strips_control_chars(self):
        self.assertEqual(sanitize_irc("a\x02b\x1fc"), "abc")

    def test_sanitize_irc_empty(self):
        self.assertEqual(sanitize_irc(""), "")
        self.assertEqual(sanitize_irc(None), "")

    def test_sanitize_log_masks_password(self):
        self.assertIn("password=***", sanitize_log("username=x&password=tajne123"))
        self.assertNotIn("tajne123", sanitize_log("username=x&password=tajne123"))

    def test_sanitize_log_masks_json_password(self):
        self.assertNotIn("tajne", sanitize_log('{"password": "tajne"}'))

    def test_sanitize_log_masks_pass_command(self):
        self.assertNotIn("secret", sanitize_log("PASS secret"))

    def test_sanitize_log_masks_tokens_and_cookies(self):
        self.assertNotIn("abc123", sanitize_log("token=abc123"))
        self.assertNotIn("sid=42", sanitize_log("Cookie: sid=42"))


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
